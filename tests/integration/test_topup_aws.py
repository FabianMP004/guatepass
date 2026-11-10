"""Opt-in real DynamoDB transaction test; creates an isolated dev/demo plate."""
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from boto3.dynamodb.conditions import Key
from common.models import stable_id

pytestmark = pytest.mark.skipif(os.getenv('RUN_TOPUP_AWS') != '1',
    reason='Requires RUN_TOPUP_AWS=1, GP_STACK and dev/demo AWS credentials')


def test_real_topups_fifo_and_duplicate_delivery():
    import boto3
    session = boto3.Session(region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
    stack = session.client('cloudformation').describe_stacks(StackName=os.getenv('GP_STACK', 'guatepass-dev'))['Stacks'][0]
    assert {p['ParameterKey']:p['ParameterValue'] for p in stack['Parameters']}['Environment'] in ('dev','demo')
    outputs = {row['OutputKey']:row['OutputValue'] for row in stack['Outputs']}
    assert outputs['BillingConsumerEnabled'] == 'true'
    ddb = session.resource('dynamodb')
    users, passes, pending, movements, idem = [ddb.Table(outputs[k+'TableName']) for k in
        ('Usuarios','Pasos','Pendientes','Movimientos','Idempotencia')]
    run = 'IT8-' + uuid.uuid4().hex
    plate, sub = run, run
    users.put_item(Item={'placa':plate,'sub':sub,'carnet':outputs['OwnerCarnetValue'],
        'saldo_centavos':0,'version':0,'prueba_id':run},ConditionExpression='attribute_not_exists(placa)')
    due = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat().replace('+00:00','Z')
    for n,amount in enumerate((800,500,500),1):
        pid = run+'-'+str(n)
        stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
        order = stamp+'#'+pid
        passes.put_item(Item={'paso_id':pid,'placa':plate,'peaje_id':'PEAJE_ZONA10','estado':'PENDIENTE',
            'version':3,'modalidad':'APP','monto_centavos':amount,'propietario_sub':sub,
            'carnet':outputs['OwnerCarnetValue'],'vence_en':due,'recibido_en':stamp,'prueba_id':run},
            ConditionExpression='attribute_not_exists(paso_id)')
        pending.put_item(Item={'placa':plate,'orden':order,'paso_id':pid,'monto_centavos':amount,
            'vence_en':due,'prueba_id':run},ConditionExpression='attribute_not_exists(placa)')
    client = session.client('lambda')
    def topup(amount,key):
        claims={'sub':sub,'custom:placa':plate,'custom:carnet':outputs['OwnerCarnetValue'],
            'token_use':'id','aud':outputs['UserPoolClientId'],'cognito:groups':['conductor']}
        payload={'pathParameters':{'placa':plate},'requestContext':{'authorizer':{'claims':claims},
            'requestId':run},'headers':{'Idempotency-Key':key},'body':json.dumps({'monto':amount})}
        response=client.invoke(FunctionName=outputs['RecargarFunctionName'],InvocationType='RequestResponse',
            Payload=json.dumps(payload).encode())
        result=json.loads(response['Payload'].read())
        assert not response.get('FunctionError'),result
        assert result['statusCode']==200,result
        return json.loads(result['body'])['data']
    print('Fixture:',run,flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=list(pool.map(lambda pair:topup(*pair),[('4.00','key-a'),('6.00','key-b')]))
    assert topup('4.00','key-a') == first
    assert topup('6.00','key-b') == second
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        rows=[passes.get_item(Key={'paso_id':run+'-'+str(i)},ConsistentRead=True)['Item'] for i in (1,2,3)]
        if rows[0]['estado']=='COBRADO':break
        time.sleep(2)
    assert [x['estado'] for x in rows]==['COBRADO','PENDIENTE','PENDIENTE']
    assert users.get_item(Key={'placa':plate},ConsistentRead=True)['Item']['saldo_centavos']==200
    assert pending.query(KeyConditionExpression=Key('placa').eq(plate),ConsistentRead=True)['Count']==2
    assert movements.get_item(Key={'movimiento_id':rows[0]['cobro_id']},ConsistentRead=True)['Item']['monto_centavos']==800
    assert sum(1 for k in ('key-a','key-b') if idem.get_item(Key={'idempotency_id':
        stable_id('idempotencia',plate,'RECARGA',k)},ConsistentRead=True).get('Item'))==2
    print('CONFIRMADO: 2 recargas Q4+Q6, repetidas sin crédito extra; Q8 cobrado, Q2 restantes, pendientes Q5 y Q5.',flush=True)
