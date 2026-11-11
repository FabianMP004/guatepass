"""Owner, role and pagination tests without modifying seeded identities."""
import base64,json
from unittest.mock import patch
from usuarios.historial import handler

CLAIMS={'sub':'s','custom:placa':'P-123ABC','token_use':'id','aud':'client','cognito:groups':['conductor']}

def req(path,claims=None,query=None):
    return {'path':path,'pathParameters':{'placa':path.split('/')[-1]},
        'requestContext':{'requestId':'test','authorizer':{'claims': claims or {}}},
        'queryStringParameters':query or {}}

def test_history_rejects_other_plate(monkeypatch):
    monkeypatch.setenv('CLIENT_ID','client');monkeypatch.setenv('ALLOWED_ORIGIN','https://demo.test')
    r=handler(req('/history/payments/P-OTHER',CLAIMS),None)
    assert r['statusCode']==403

def test_history_invalid_cursor_rejected_before_query(monkeypatch):
    monkeypatch.setenv('CLIENT_ID','client');monkeypatch.setenv('ALLOWED_ORIGIN','https://demo.test')
    r=handler(req('/history/invoices/P-123ABC',CLAIMS,{'cursor':'@'}),None)
    assert r['statusCode']==400

def test_history_requires_conductor(monkeypatch):
    monkeypatch.setenv('CLIENT_ID','client');monkeypatch.setenv('ALLOWED_ORIGIN','https://demo.test')
    assert handler(req('/history/payments/P-123ABC',dict(CLAIMS,**{'cognito:groups':['operador']})),None)['statusCode']==403

def test_invoice_projection_and_pagination(monkeypatch):
    import boto3
    from moto import mock_aws
    monkeypatch.setenv('AWS_DEFAULT_REGION','us-east-1')
    monkeypatch.setenv('CLIENT_ID','client');monkeypatch.setenv('ALLOWED_ORIGIN','https://demo.test')
    with mock_aws():
        db=boto3.resource('dynamodb')
        table=db.create_table(TableName='facturas-test',BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ('factura_id','placa','orden')],
            KeySchema=[{'AttributeName':'factura_id','KeyType':'HASH'}],
            GlobalSecondaryIndexes=[{'IndexName':'PorPlaca','KeySchema':[
                {'AttributeName':'placa','KeyType':'HASH'},{'AttributeName':'orden','KeyType':'RANGE'}],
                'Projection':{'ProjectionType':'ALL'}}])
        monkeypatch.setenv('FACTURAS_TABLE','facturas-test')
        for n in (1,2):
            table.put_item(Item={'factura_id':'f'+str(n),'placa':'P-123ABC',
                'paso_id':'p'+str(n),'orden':'2026-11-10#'+str(n),'numero':'GP-'+str(n),
                'fecha':'2026-11-10','monto_centavos':2950,'concepto':'PLAZO_VENCIDO',
                'estado':'POR_COBRAR','NIT':'9012345-9','nombre':'Andrés'})
        first=json.loads(handler(req('/history/invoices/P-123ABC',CLAIMS,{'limit':'1'}),None)['body'])['data']
        assert len(first['items'])==1 and first['next_cursor']
        assert first['items'][0]['contribuyente']['nit']=='9012345-9'
        assert first['items'][0]['monto']=='29.50'
        next_page=json.loads(handler(req('/history/invoices/P-123ABC',CLAIMS,{'limit':'1','cursor':first['next_cursor']}),None)['body'])['data']
        assert next_page['items'][0]['factura_id']!=first['items'][0]['factura_id']
        assert handler(req('/history/invoices/P-OTHER',CLAIMS,{'cursor':first['next_cursor']}),None)['statusCode']==403
