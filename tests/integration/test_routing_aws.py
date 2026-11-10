"""Explicit AWS integration; creates simulated notifications, never changes balances.
Slow DLQ test uses actual configured redrive, not manual forwarding.
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone
import boto3
import pytest
from common.models import make_event

pytestmark=pytest.mark.skipif(os.getenv('RUN_AWS_INTEGRATION')!='1',reason='Requiere opt-in AWS')

@pytest.fixture(scope='module')
def aws():
    session=boto3.Session(region_name=os.getenv('AWS_REGION','us-east-1'))
    stack=session.client('cloudformation').describe_stacks(StackName=os.getenv('GP_STACK','guatepass-dev'))['Stacks'][0]
    params={p['ParameterKey']:p['ParameterValue'] for p in stack['Parameters']}
    assert params['Environment'] in ('dev','demo'), 'Solo dev/demo'
    return session,{o['OutputKey']:o['OutputValue'] for o in stack['Outputs']}

def sample(o):
    identifier='routing-test-'+uuid.uuid4().hex
    # Only notification rules match invitations; no billing/registry consumers.
    return make_event('invitacion.solicitada',identifier,1,
        {'paso_id':identifier,'placa':'TEST-'+uuid.uuid4().hex,
         'contacto':{'telefono':'50200000000'}},identifier,
        datetime.now(timezone.utc).isoformat(),o['OwnerCarnetValue'])

def test_deployed_patterns_and_wrong_source(aws):
    session,o=aws;events=session.client('events')
    cases=[('guatepass.pasos','paso.registrado',{'Pasos'}),
           ('guatepass.pasos','paso.no_registrado',{'Registro'}),
           ('guatepass.saldo','saldo.recargado',{'Pasos','Notificaciones'}),
           ('guatepass.registro','invitacion.solicitada',{'Notificaciones'}),
           ('guatepass.pasos','invitacion.solicitada',set()),
           ('guatepass','PasoRegistrado',{'PasosLegacy'})]
    patterns={}
    for name in ('Pasos','Registro','Notificaciones','PasosLegacy','RegistroLegacy','NotificacionesLegacy'):
        rule_arn=o[name+'RuleArn']
        patterns[name]=events.describe_rule(Name=rule_arn.rsplit('/',1)[1],EventBusName=o['EventBusName'])['EventPattern']
    for source,kind,expected in cases:
        event={'version':'0','id':str(uuid.uuid4()),'account':session.client('sts').get_caller_identity()['Account'],
            'time':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'region':session.region_name,'resources':[],
            'source':source,'detail-type':kind,'detail':{}}
        actual={name for name,pattern in patterns.items() if events.test_event_pattern(EventPattern=pattern,Event=json.dumps(event))['Result']}
        assert actual==expected,(source,kind,actual)

def test_real_delivery_and_repeated_message(aws):
    session,o=aws;event=sample(o)
    table=session.resource('dynamodb').Table(o['NotificacionesTableName'])
    key={'notificacion_id':event['detail']['evento_id']+'#placa:'+event['detail']['placa']}
    def publish():
        result=session.client('events').put_events(Entries=[{'EventBusName':o['EventBusName'],
            'Source':event['source'],'DetailType':event['detail-type'],'Detail':json.dumps(event['detail'])}])
        assert result['FailedEntryCount']==0 and result['Entries'][0].get('EventId')
    publish()
    before=None
    for _ in range(45):
        before=table.get_item(Key=key,ConsistentRead=True).get('Item')
        if before:break
        time.sleep(2)
    assert before and before['canal']=='SMS_SIMULADO'
    publish()
    # Invoke the same SQS handler synchronously to prove duplicate processing
    # completed, rather than assume eventual consumption after an arbitrary sleep.
    payload={'Records':[{'messageId':'test-duplicate-'+uuid.uuid4().hex,'body':json.dumps(event)}]}
    result=session.client('lambda').invoke(FunctionName=o['NotificarFunctionName'],Payload=json.dumps(payload).encode())
    body=json.loads(result['Payload'].read())
    assert not result.get('FunctionError') and body=={'batchItemFailures':[]}
    assert table.get_item(Key=key,ConsistentRead=True)['Item']==before
    print('Notificación simulada de evidencia:',key['notificacion_id'])

@pytest.mark.skipif(os.getenv('RUN_DLQ_TEST')!='1',reason='Redrive real tarda aproximadamente 15–20 minutos')
def test_malformed_message_reaches_consumer_dlq(aws):
    session,o=aws;sqs=session.client('sqs')
    marker='malformed-routing-test-'+uuid.uuid4().hex
    body=json.dumps({'test_id':marker})
    sqs.send_message(QueueUrl=o['NotificacionesQueueUrl'],MessageBody=body)
    deadline=time.monotonic()+1200
    print('Esperando redrive real a DLQ; marcador:',marker,flush=True)
    while time.monotonic()<deadline:
        response=sqs.receive_message(QueueUrl=o['NotificacionesDLQUrl'],MaxNumberOfMessages=10,
            WaitTimeSeconds=20,VisibilityTimeout=5)
        found=False
        for message in response.get('Messages',[]):
            if message['Body']==body:found=True
            # Keep evidence and immediately release any unrelated DLQ messages.
            sqs.change_message_visibility(QueueUrl=o['NotificacionesDLQUrl'],ReceiptHandle=message['ReceiptHandle'],VisibilityTimeout=0)
        if found:
            print('Mensaje malformado llegó a la DLQ por redrive configurado.',flush=True)
            return
        time.sleep(3)
    pytest.fail('No llegó a DLQ en 20 minutos: revisar logs, trigger y redrive.')
