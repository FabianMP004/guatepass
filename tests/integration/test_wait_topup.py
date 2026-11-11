"""Business invariants of the Standard wait and the expired invoice (Moto)."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import boto3
import pytest
from moto import mock_aws
from common.models import stable_id
from common import money
from pasos.facturar import facturar_paso
from flujos.iniciar import preparar, iniciar
from flujos.registrar_token import registrar_token
from flujos.completar import completar
from flujos.vencer import vencer

PLATE = 'P-666YZA'

def iso(dt): return dt.isoformat(timespec='seconds').replace('+00:00','Z')

@pytest.fixture
def rows(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION','us-east-1')
    monkeypatch.setenv('STATE_MACHINE_ARN','arn:aws:states:us-east-1:123456789012:stateMachine:prueba')
    with mock_aws():
        db=boto3.resource('dynamodb');out={}
        for name,keys in {'USUARIOS':['placa'],'PASOS':['paso_id'],'PENDIENTES':['placa','orden'],
            'FACTURAS':['factura_id'],'OUTBOX':['evento_id'],'ESPERAS':['espera_id'],
            'MOVIMIENTOS':['movimiento_id']}.items():
            monkeypatch.setenv(name+'_TABLE',name.lower())
            out[name]=db.create_table(TableName=name.lower(),BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in keys],
                KeySchema=[{'AttributeName':k,'KeyType':'HASH' if n==0 else 'RANGE'} for n,k in enumerate(keys)])
        out['USUARIOS'].put_item(Item={'placa':PLATE,'sub':'andres','carnet':'20230223',
            'saldo_centavos':0,'version':0,'nombre':'Andrés Castillo','NIT':'9012345-9'})
        yield out

def pending(rows, pid='paso-1', due=None):
    due=iso(due or (datetime.now(timezone.utc)+timedelta(minutes=3)))
    received=iso(datetime.now(timezone.utc)-timedelta(minutes=1))
    rows['PASOS'].put_item(Item={'paso_id':pid,'placa':PLATE,'peaje_id':'PEAJE_ZONA10',
        'estado':'PENDIENTE','version':3,'modalidad':'TAG','monto_centavos':450,
        'propietario_sub':'andres','carnet':'20230223','vence_en':due,'recibido_en':received})
    rows['PENDIENTES'].put_item(Item={'placa':PLATE,'orden':received+'#'+pid,
        'paso_id':pid,'monto_centavos':450,'vence_en':due})
    return pid

def get(rows,name,key): return rows[name].get_item(Key=key,ConsistentRead=True).get('Item')

def test_iniciar_uses_original_deadline_and_deterministic_name(rows):
    pid=pending(rows)
    first=preparar(pid)
    assert first['status']=='WAIT' and first['seconds'] <= 180 and first['seconds']>0
    with patch('flujos.iniciar._sfn_client') as client:
        client.return_value.start_execution.return_value={'executionArn':'arn:example'}
        assert iniciar(pid)==iniciar(pid)
        assert [c.kwargs['name'] for c in client.return_value.start_execution.call_args_list]==[pid,pid]

def test_duplicate_callback_and_early_credit_do_not_invoice(rows):
    pid=pending(rows)
    execution='arn:aws:states:us-east-1:123456789012:execution:prueba:'+pid
    # Simulate payment before the callback token exists.
    paso=get(rows,'PASOS',{'paso_id':pid})
    rows['PASOS'].put_item(Item=dict(paso,estado='COBRADO',version=4,cobro_id='cobro-1'))
    with patch('flujos.completar.send_success') as send:
        assert completar(pid)=='SENAL_GUARDADA'
        assert completar(pid)=='SENAL_GUARDADA'
        registrar_token({'paso_id':pid,'token':'secret-token','execution_arn':execution},None)
        send.assert_called_once()
    assert 'token' not in str(get(rows,'ESPERAS',{'espera_id':'RECARGA#'+pid}))
    assert facturar_paso(pid)=='SIN_CAMBIO'
    assert rows['FACTURAS'].scan()['Count']==0

def test_expired_andres_invoice_has_surcharge_and_fiscal_snapshot(rows):
    pid=pending(rows,due=datetime.now(timezone.utc)-timedelta(seconds=2))
    assert preparar(pid)['status']=='VENCIDO'
    assert vencer({'paso_id':pid},None)=='FACTURADO'
    assert vencer({'paso_id':pid},None)=='SIN_CAMBIO'
    invoice=get(rows,'FACTURAS',{'factura_id':stable_id('factura',pid,'PLAZO_VENCIDO')})
    assert invoice['monto_centavos']==2950
    assert invoice['NIT']=='9012345-9'
    assert invoice['concepto']=='PLAZO_VENCIDO'
    assert get(rows,'USUARIOS',{'placa':PLATE})['saldo_centavos']==0
    assert get(rows,'PASOS',{'paso_id':pid})['estado']=='FACTURADO'
    assert rows['PENDIENTES'].scan()['Count']==0
    assert rows['OUTBOX'].scan()['Count']==1
    assert rows['MOVIMIENTOS'].scan()['Count']==0

def test_payment_at_expiry_has_one_winner(rows):
    pid=pending(rows,due=datetime.now(timezone.utc)-timedelta(seconds=2))
    assert facturar_paso(pid)=='FACTURADO'
    assert money.cobrar_paso(pid)=='SIN_CAMBIO'
    assert rows['FACTURAS'].scan()['Count']==1

def test_vigente_no_se_factura(rows):
    pid=pending(rows)
    assert facturar_paso(pid)=='NO_VENCIDO'
    assert rows['FACTURAS'].scan()['Count']==0
