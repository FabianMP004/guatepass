"""Task 8 integration against isolated DynamoDB transactions (Moto)."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from common.models import make_event
from common import money
from pasos.pendientes import cobrar_pendientes
from usuarios.recargar import recargar, handler as api_handler
from pasos.cobrar import handler as queue_handler


@pytest.fixture
def wallet(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://demo.local')
    monkeypatch.setenv('CLIENT_ID', 'client')
    monkeypatch.setenv('OWNER_CARNET', '20230223')
    monkeypatch.setenv('PASOS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/123456789012/pasos')
    with mock_aws():
        ddb = boto3.resource('dynamodb')
        tables = {}
        for name, keys in {'USUARIOS':['placa'], 'IDEMPOTENCIA':['idempotency_id'],
                'MOVIMIENTOS':['movimiento_id'], 'OUTBOX':['evento_id'],
                'PASOS':['paso_id'], 'PENDIENTES':['placa','orden'],
                'ESPERAS':['espera_id'], 'PEAJES':['peaje_id'], 'TAGS':['tag_id']}.items():
            monkeypatch.setenv(name+'_TABLE', name.lower())
            tables[name]=ddb.create_table(TableName=name.lower(), BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in keys],
                KeySchema=[{'AttributeName':k,'KeyType':'HASH' if i==0 else 'RANGE'}
                    for i,k in enumerate(keys)])
        tables['USUARIOS'].put_item(Item={'placa':'P-444STU','sub':'sub-diego',
            'carnet':'20230223','saldo_centavos':0,'version':0})
        monkeypatch.setenv('RECARGA_TIMEOUT_PARAMETER','/test/timeout')
        boto3.client('ssm').put_parameter(Name='/test/timeout',Value='86400',Type='String')
        yield tables


ACTOR={'sub':'sub-diego','custom:placa':'P-444STU','custom:carnet':'20230223'}


def item(tables, name, key):
    return tables[name].get_item(Key=key,ConsistentRead=True).get('Item')


def count(tables, name):
    return tables[name].scan(ConsistentRead=True)['Count']


def add_pending(tables, identifier, amount, order, *, expired=False):
    due=(datetime.now(timezone.utc)+timedelta(days=-1 if expired else 1)).isoformat().replace('+00:00','Z')
    when='2026-11-10T12:00:%02d.000000Z'%order
    p={'paso_id':identifier,'placa':'P-444STU','peaje_id':'PEAJE_ZONA10',
       'estado':'PENDIENTE','version':3,'modalidad':'APP','monto_centavos':amount,
       'propietario_sub':'sub-diego','carnet':'20230223','vence_en':due,'recibido_en':when,
       'fecha_local':'2026-11-10'}
    tables['PASOS'].put_item(Item=p)
    tables['PENDIENTES'].put_item(Item={'placa':'P-444STU','orden':when+'#'+identifier,
        'paso_id':identifier,'monto_centavos':amount,'vence_en':due})


def test_repeat_key_replays_response_without_another_credit(wallet):
    first=recargar('P-444STU',1000,'key-1',ACTOR)
    again=recargar('P-444STU',1000,'key-1',ACTOR)
    assert again==first
    assert first['saldo_despues_recarga']=='10.00'
    assert item(wallet,'USUARIOS',{'placa':'P-444STU'})['saldo_centavos']==1000
    assert count(wallet,'MOVIMIENTOS')==count(wallet,'IDEMPOTENCIA')==count(wallet,'OUTBOX')==1
    with pytest.raises(ValueError,match='IDEMPOTENCY_CONFLICT'):
        recargar('P-444STU',500,'key-1',ACTOR)


def test_two_simultaneous_topups(wallet):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda v: recargar('P-444STU',v[0],v[1],ACTOR),[(500,'a'),(700,'b')]))
    assert len(results)==2
    assert item(wallet,'USUARIOS',{'placa':'P-444STU'})['saldo_centavos']==1200
    assert count(wallet,'MOVIMIENTOS')==count(wallet,'IDEMPOTENCIA')==2
    assert item(wallet,'USUARIOS',{'placa':'P-444STU'})['version']==2


def test_order_stops_at_first_insufficient(wallet):
    for order,amount in enumerate((800,500,500),1):
        add_pending(wallet,'p'+str(order),amount,order)
    recargar('P-444STU',1000,'k',ACTOR)
    assert cobrar_pendientes('P-444STU')==['p1']
    assert item(wallet,'USUARIOS',{'placa':'P-444STU'})['saldo_centavos']==200
    assert [item(wallet,'PASOS',{'paso_id':'p'+str(i)})['estado'] for i in (1,2,3)]==['COBRADO','PENDIENTE','PENDIENTE']
    assert count(wallet,'PENDIENTES')==2
    assert cobrar_pendientes('P-444STU')==[]


def test_expired_first_is_not_skipped(wallet):
    add_pending(wallet,'expired',500,1,expired=True)
    add_pending(wallet,'younger',100,2)
    recargar('P-444STU',1000,'k',ACTOR)
    assert cobrar_pendientes('P-444STU')==[]
    assert count(wallet,'MOVIMIENTOS')==1


def test_concurrent_drain_and_replay(wallet):
    for order in (1,2):add_pending(wallet,'p'+str(order),500,order)
    recargar('P-444STU',1000,'k',ACTOR)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(cobrar_pendientes,['P-444STU','P-444STU']))
    assert sorted(p for part in results for p in part)==['p1','p2']
    assert item(wallet,'USUARIOS',{'placa':'P-444STU'})['saldo_centavos']==0
    assert count(wallet,'MOVIMIENTOS')==3


def test_race_new_pending_after_credit_requests_drain(wallet):
    recargar('P-444STU',500,'k',ACTOR)
    # A registration classified on a previous low balance can have an old
    # consistent read of 0; its conditional transaction must re-read first.
    add_pending(wallet,'late',500,1)
    event=make_event('paso.pendiente','late',3,{'paso_id':'late','placa':'P-444STU',
        'peaje_id':'PEAJE_ZONA10','monto_centavos':500,
        'vence_en':item(wallet,'PASOS',{'paso_id':'late'})['vence_en']},
        'req','2026-11-10T12:00:00Z','20230223')
    assert queue_handler({'Records':[{'messageId':'m','body':json.dumps(event)}]},None)=={'batchItemFailures':[]}
    assert item(wallet,'PASOS',{'paso_id':'late'})['estado']=='COBRADO'


def test_long_drain_saves_continuation(wallet,monkeypatch):
    for i in range(1,5):add_pending(wallet,'p'+str(i),100,i)
    recargar('P-444STU',500,'k',ACTOR)
    monkeypatch.setattr('pasos.pendientes.MAX_PER_RUN',2)
    with patch('pasos.pendientes.send_continuation') as send:
        assert cobrar_pendientes('P-444STU')==['p1','p2']
        assert item(wallet,'ESPERAS',{'espera_id':'DRENAJE#P-444STU'})
        assert send.call_count==1
        assert cobrar_pendientes('P-444STU')==['p3','p4']
    assert count(wallet,'ESPERAS')==0


@pytest.mark.parametrize('invalid', [0,-1,True,1.5,1000001])
def test_amount_invalid(wallet,invalid):
    with pytest.raises(ValueError):recargar('P-444STU',invalid,'key',ACTOR)
    assert count(wallet,'MOVIMIENTOS')==0


def test_wrong_actor_cannot_fund_other_plate(wallet):
    recargar('P-444STU',500,'k',ACTOR)
    with pytest.raises(ValueError):recargar('P-444STU',500,'k',dict(ACTOR,sub='other'))
    assert count(wallet,'MOVIMIENTOS')==1


def test_http_decimal_validation_and_missing_key(wallet):
    def request(value,key='key'):
        return {'pathParameters':{'placa':'P-444STU'},'requestContext':{
            'requestId':'req','authorizer':{'claims':dict(ACTOR,token_use='id',aud='client',
                **{'cognito:groups':'conductor'})}},'body':json.dumps({'monto':value}),
            'headers':{'Idempotency-Key':key}}
    for value in ('NaN','Infinity','0','-1','1.001','10000.01',None):
        assert api_handler(request(value),None)['statusCode']==400
    assert api_handler(request('10.00',''),None)['statusCode']==400
    answer=api_handler(request('5.00'),None)
    assert answer['statusCode']==200
    assert json.loads(answer['body'])['data']['monto']=='5.00'
