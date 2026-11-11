"""Opt-in Step Functions Standard proof on isolated development fixtures.

Prints execution ARNs for the Graph view. Fixture rows stay for audit.
"""
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
import pytest

pytestmark=pytest.mark.skipif(os.getenv('RUN_WAIT_AWS')!='1',reason='Needs deployed dev/demo stack and RUN_WAIT_AWS=1')


def test_running_then_succeeded_is_business_consistent():
    import boto3
    from common.models import stable_id
    session=boto3.Session(region_name=os.getenv('AWS_DEFAULT_REGION','us-east-1'))
    stack=session.client('cloudformation').describe_stacks(StackName=os.getenv('GP_STACK','guatepass-dev'))['Stacks'][0]
    assert {p['ParameterKey']:p['ParameterValue'] for p in stack['Parameters']}['Environment'] in ('dev','demo')
    o={x['OutputKey']:x['OutputValue'] for x in stack['Outputs']}
    assert o['BillingConsumerEnabled']=='true'
    db=session.resource('dynamodb')
    users,passes,pending,invoice,movements=[db.Table(o[k+'TableName']) for k in
        ('Usuarios','Pasos','Pendientes','Facturas','Movimientos')]
    sfn=session.client('stepfunctions');lam=session.client('lambda')
    marker='IT9-'+uuid.uuid4().hex
    def make(n,seconds):
        pid=marker+'-'+n;plate=marker+'-p'+n
        stamp=datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
        due=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='seconds').replace('+00:00','Z')
        users.put_item(Item={'placa':plate,'sub':plate,'carnet':o['OwnerCarnetValue'],'saldo_centavos':0,
            'version':0,'nombre':'Andrés test','NIT':'9012345-9','prueba_id':marker},ConditionExpression='attribute_not_exists(placa)')
        passes.put_item(Item={'paso_id':pid,'placa':plate,'peaje_id':'PEAJE_ZONA10','estado':'PENDIENTE',
            'version':3,'modalidad':'TAG','monto_centavos':450,'propietario_sub':plate,
            'carnet':o['OwnerCarnetValue'],'vence_en':due,'recibido_en':stamp,'prueba_id':marker},
            ConditionExpression='attribute_not_exists(paso_id)')
        pending.put_item(Item={'placa':plate,'orden':stamp+'#'+pid,'paso_id':pid,'monto_centavos':450,
            'vence_en':due,'prueba_id':marker},ConditionExpression='attribute_not_exists(placa)')
        return pid,plate
    def state(pid):return passes.get_item(Key={'paso_id':pid},ConsistentRead=True)['Item']['estado']
    def execution(pid):
        reply=sfn.start_execution(stateMachineArn=o['EsperaRecargaArn'],name=pid,input=json.dumps({'paso_id':pid}))
        return reply['executionArn']
    def wait(arn,final,limit):
        until=time.monotonic()+limit
        while time.monotonic()<until:
            status=sfn.describe_execution(executionArn=arn)['status']
            if status=='SUCCEEDED':return
            assert status=='RUNNING',status
            time.sleep(2)
        raise AssertionError('Execution still RUNNING after deadline: '+arn)
    pid,plate=make('paid',75)
    arn=execution(pid)
    assert sfn.describe_execution(executionArn=arn)['status']=='RUNNING'
    print('Graph RUNNING:',arn,flush=True)
    client_id=o['UserPoolClientId']
    claims={'sub':plate,'custom:placa':plate,'custom:carnet':o['OwnerCarnetValue'],
        'token_use':'id','aud':client_id,'cognito:groups':['conductor']}
    event={'pathParameters':{'placa':plate},'requestContext':{'authorizer':{'claims':claims},
        'requestId':marker},'headers':{'Idempotency-Key':marker},'body':json.dumps({'monto':'4.50'})}
    response=lam.invoke(FunctionName=o['RecargarFunctionName'],Payload=json.dumps(event).encode())
    result=json.loads(response['Payload'].read())
    assert not response.get('FunctionError') and result['statusCode']==200,result
    wait(arn,'COBRADO',80)
    assert state(pid)=='COBRADO'
    assert invoice.get_item(Key={'factura_id':stable_id('factura',pid,'PLAZO_VENCIDO')},ConsistentRead=True).get('Item') is None
    print('Graph SUCCEEDED + paso COBRADO:',arn,flush=True)
    expired,plate2=make('expired',9)
    arn2=execution(expired)
    assert sfn.describe_execution(executionArn=arn2)['status']=='RUNNING'
    print('Graph RUNNING:',arn2,flush=True)
    wait(arn2,'FACTURADO',50)
    assert state(expired)=='FACTURADO'
    row=invoice.get_item(Key={'factura_id':stable_id('factura',expired,'PLAZO_VENCIDO')},ConsistentRead=True)['Item']
    assert row['monto_centavos']==2950 and row['NIT']=='9012345-9'
    assert users.get_item(Key={'placa':plate2},ConsistentRead=True)['Item']['saldo_centavos']==0
    assert not movements.get_item(Key={'movimiento_id':stable_id('cobro',expired)},ConsistentRead=True).get('Item')
    print('Graph SUCCEEDED + paso FACTURADO Q29.50 sin cobro:',arn2,flush=True)
