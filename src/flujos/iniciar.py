"""Start exactly one named Standard execution for a pending pass."""
import json
import logging
import os
from datetime import datetime, timezone
import boto3
from common import dynamo as d
from common.models import validate_event
from common.catalogo import LEGACY
from pasos.facturar import facturar_paso


def preparar(paso_id):
    row=d.get(d.client(),'PASOS',{'paso_id':paso_id})
    if not row or row['estado']!='PENDIENTE':return {'status':'TERMINADO','seconds':1}
    due=datetime.fromisoformat(row['vence_en'].replace('Z','+00:00'))
    if due.tzinfo is None:raise ValueError('INVALID_DEADLINE')
    remaining=(due-datetime.now(timezone.utc)).total_seconds()
    if remaining<=0:return {'status':'VENCIDO','seconds':1}
    # Round up; the final transaction checks the original persisted deadline.
    import math
    return {'status':'WAIT','seconds':max(1,math.ceil(remaining))}


def _sfn_client():
    return boto3.client('stepfunctions')


def iniciar(paso_id):
    if not isinstance(paso_id,str) or not paso_id or len(paso_id)>80:
        raise ValueError('INVALID_PASO_ID')
    state=preparar(paso_id)
    if state['status']=='TERMINADO':return 'SIN_CAMBIO'
    if state['status']=='VENCIDO':return facturar_paso(paso_id)
    client=_sfn_client()
    try:
        reply=client.start_execution(stateMachineArn=os.environ['STATE_MACHINE_ARN'],
            name=paso_id,input=json.dumps({'paso_id':paso_id},separators=(',',':')))
        return reply['executionArn']
    except client.exceptions.ExecutionAlreadyExists:
        # Standard execution names cannot be reused after success; duplicate
        # delivery must not extend the original deadline or create another wait.
        arn=os.environ['STATE_MACHINE_ARN'].replace(':stateMachine:',':execution:')+':'+paso_id
        execution=client.describe_execution(executionArn=arn)
        if execution['status']=='FAILED':
            raise RuntimeError('FAILED_WAIT_EXECUTION_REQUIRES_REDRIVE')
        return arn


def handler(event,context):
    if event.get('check_only') is True:
        return preparar(event['paso_id'])
    failures=[]
    for record in event['Records']:
        try:
            msg=json.loads(record['body']);validate_event(msg)
            if LEGACY.get(msg['detail-type'],msg['detail-type'])!='paso.pendiente':
                raise ValueError('UNSUPPORTED_WAIT_EVENT')
            iniciar(msg['detail']['paso_id'])
        except Exception as exc:
            logging.error('wait start failed message_id=%s error_type=%s',record['messageId'],type(exc).__name__)
            failures.append({'itemIdentifier':record['messageId']})
    return {'batchItemFailures':failures}


def check_handler(event,context):
    return preparar(event['paso_id'])
