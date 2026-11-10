import json
from unittest.mock import patch, Mock
import boto3
import pytest
from moto import mock_aws
from pasos import webhook
from common.models import paso_id

@pytest.fixture
def db(monkeypatch):
    for key, value in {'AWS_DEFAULT_REGION': 'us-east-1', 'PASOS_TABLE': 'pasos',
                       'PEAJES_TABLE': 'peajes', 'OUTBOX_TABLE': 'outbox',
                       'OWNER_CARNET': '20230223', 'ALLOWED_ORIGIN': 'https://example.com'}.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        client = boto3.client('dynamodb')
        for table, key in [('pasos','paso_id'),('peajes','peaje_id'),('outbox','evento_id')]:
            client.create_table(TableName=table, BillingMode='PAY_PER_REQUEST',
                KeySchema=[{'AttributeName':key,'KeyType':'HASH'}],
                AttributeDefinitions=[{'AttributeName':key,'AttributeType':'S'}])
        client.put_item(TableName='peajes',Item={'peaje_id':{'S':'T1'}})
        monkeypatch.setattr(webhook, '_ddb', client)
        yield client

def aviso(**kwargs):
    return dict(placa='P-456DEF', peaje_id='T1', timestamp='2026-11-09T12:00:00Z', tag_id=None, **kwargs)

def test_atomic_replay_preserves_first_payload(db):
    a = aviso()
    first = webhook.registrar_paso(a)
    a.update(placa=' p-456def ',timestamp='2026-11-09T12:00:00.000000+00:00',tag_id='TAG-001')
    assert webhook.registrar_paso(a) == first
    assert db.scan(TableName='pasos')['Count'] == db.scan(TableName='outbox')['Count'] == 1
    row = db.scan(TableName='pasos')['Items'][0]
    assert row['tag_id'] == {'NULL': True}
    assert row['fecha_local']['S'] == '2026-11-09'
    assert 'recibido_en' in row
    event = json.loads(db.scan(TableName='outbox')['Items'][0]['detail']['S'])
    assert event['carnet'] == '20230223'

def test_missing_toll_rolls_back_both(db):
    a = aviso(); a['peaje_id'] = 'absent'
    with pytest.raises(webhook.TollNotFound): webhook.registrar_paso(a)
    assert db.scan(TableName='pasos')['Count'] == db.scan(TableName='outbox')['Count'] == 0

@pytest.mark.parametrize('field,value', [('placa','bad'),('placa',None),('peaje_id',False),
    ('timestamp','2026-11-09T12:00:00.1234567Z'),('timestamp','2026-11-09T12:00:00'),
    ('timestamp','2026-11-09T06:00:00-06:00'),('timestamp','2026-02-30T12:00:00Z'),
    ('tag_id',False),('tag_id',''),('carnet','spoof')])
def test_invalid_inputs(field,value):
    a = aviso(); a[field] = value
    with pytest.raises(ValueError): webhook.normalize(a)

def test_microseconds_not_collapsed():
    assert paso_id('P-456DEF','T1','2026-11-09T12:00:00.000001Z') != paso_id('P-456DEF','T1','2026-11-09T12:00:00.000002Z')

def test_handler_returns_202_for_replay(db):
    request = {'body':json.dumps(aviso()),'requestContext':{'requestId':'req'}}
    a = webhook.handler(request,None); b = webhook.handler(request,None)
    assert a['statusCode'] == b['statusCode'] == 202
    assert json.loads(a['body'])['data'] == json.loads(b['body'])['data']

def test_failed_persistence_never_acknowledges(db):
    with patch.object(db,'transact_write_items',side_effect=RuntimeError('down')):
        assert webhook.handler({'body':json.dumps(aviso())},None)['statusCode'] == 503

def test_replay_preserves_paid_state(db):
    result = webhook.registrar_paso(aviso())
    db.update_item(TableName='pasos',Key=webhook.encode(result),
        UpdateExpression='SET estado=:s',ExpressionAttributeValues={':s':{'S':'COBRADO'}})
    assert webhook.registrar_paso(aviso()) == result
    assert db.get_item(TableName='pasos',Key=webhook.encode(result))['Item']['estado']['S'] == 'COBRADO'
