"""Local Moto tests. Real routing/redrive is in test_routing_aws.py."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import boto3
import pytest
import yaml
from moto import mock_aws
from common.models import make_event
from eventos.notificar import notificar, handler

def notification(kind='saldo.recargado', payload=None, identifier='r1'):
    return make_event(kind,identifier,1,payload or {'recarga_id':identifier,'placa':'P-456DEF','monto_centavos':2000,'saldo_centavos':2500},
                      'req','2026-11-09T12:00:00Z','20230223')

@pytest.fixture
def tables(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION','us-east-1')
    monkeypatch.setenv('NOTIFICACIONES_TABLE','notificaciones')
    monkeypatch.setenv('USUARIOS_TABLE','usuarios')
    with mock_aws():
        db=boto3.resource('dynamodb')
        for name,key in [('notificaciones','notificacion_id'),('usuarios','placa')]:
            db.create_table(TableName=name,BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName':key,'AttributeType':'S'}],
                KeySchema=[{'AttributeName':key,'KeyType':'HASH'}])
        users=db.Table('usuarios')
        users.put_item(Item={'placa':'P-456DEF','email':'demo@example.com','saldo_centavos':99999})
        yield db.Table('notificaciones'),users

def test_duplicate_is_one_record_and_uses_snapshot(tables):
    table,users=tables
    event=notification();notificar(event)
    before=table.scan()['Items']
    users.update_item(Key={'placa':'P-456DEF'},UpdateExpression='SET saldo_centavos=:v',ExpressionAttributeValues={':v':1})
    notificar(dict(event,id='different-eventbridge-id',**{'replay-name':'replay-test'}))
    assert table.scan()['Items']==before
    row=before[0]
    assert row['notificacion_id']==event['detail']['evento_id']+'#placa:P-456DEF'
    assert 'Q25.00' in row['mensaje']
    assert row['canal']=='EMAIL_SIMULADO'

def test_concurrent_duplicates_use_conditional_write(tables):
    event=notification()
    # Boto3 sessions are constructed in each worker, not shared resource instances.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(notificar,[event,event]))
    assert tables[0].scan()['Count']==1

def test_same_id_different_content_is_rejected(tables):
    event=notification();notificar(event)
    event['detail']['monto_centavos']=3000
    with pytest.raises(ValueError,match='CONTENT_CONFLICT'):notificar(event)

def test_phone_only_invitation_is_simulated(tables):
    notificar(notification('invitacion.solicitada',{'paso_id':'p1','placa':'P-999ZZZ','contacto':{'telefono':'50212345678'}}))
    row=tables[0].scan()['Items'][0]
    assert row['canal']=='SMS_SIMULADO' and row['simulada'] is True
    assert row['destinatario']=='placa:P-999ZZZ'

def test_finance_recipient(tables):
    notificar(notification('recaudacion.calculada',{'reporte_id':'rep1','fecha':'2026-11-09','calculado_en':'2026-11-10T12:00:00Z'}))
    assert tables[0].scan()['Items'][0]['destinatario']=='finanzas'

def test_partial_batch_retries_only_invalid(tables):
    result=handler({'Records':[{'messageId':'bad','body':'not-json'},
                              {'messageId':'good','body':json.dumps(notification())}]},None)
    assert result=={'batchItemFailures':[{'itemIdentifier':'bad'}]}
    assert tables[0].scan()['Count']==1

def test_transient_storage_error_is_retried(tables):
    with patch('eventos.notificar.notificar',side_effect=RuntimeError('ddb unavailable')):
        assert handler({'Records':[{'messageId':'m','body':json.dumps(notification())}]},None)=={'batchItemFailures':[{'itemIdentifier':'m'}]}

def test_legacy_notification_stays_legacy(tables):
    event=notification('RecargaAplicada');notificar(event);notificar(event)
    row=tables[0].scan()['Items'][0]
    assert row['evento_id']==event['detail']['evento_id']
    assert row['source']=='guatepass'

def test_template_retry_and_retention_invariants():
    template=yaml.safe_load((Path(__file__).resolve().parents[2]/'template.yaml').read_text())
    r=template['Resources'];fn=r['NotificarFunction']['Properties']
    sqs=fn['Events']['Messages']['Properties']
    for name in ('Pasos','Registro','Notificaciones'):
        queue=r[name+'Queue']['Properties'];dlq=r[name+'DLQ']['Properties']
        assert queue['VisibilityTimeout']>=6*fn['Timeout']+sqs['MaximumBatchingWindowInSeconds']
        assert dlq['MessageRetentionPeriod']>queue['MessageRetentionPeriod']
        assert queue['RedrivePolicy']['maxReceiveCount']==5
        assert dlq['RedriveAllowPolicy']['redrivePermission']=='byQueue'
        statements=r[name+'QueuePolicy']['Properties']['PolicyDocument']['Statement']
        assert all('aws:SourceArn' in s['Condition']['ArnEquals'] for s in statements)
    assert sqs['FunctionResponseTypes']==['ReportBatchItemFailures']
    assert r['GuatePassArchive']['Properties']['RetentionDays']==30
    for name in ('PasosRule','RegistroRule','NotificacionesRule','PasosLegacyRule','RegistroLegacyRule','NotificacionesLegacyRule'):
        assert r[name]['Properties']['Targets'][0]['DeadLetterConfig']['Arn']=={'Fn::GetAtt':['RoutingDeliveryDLQ','Arn']}
