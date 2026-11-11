"""Persist a paid signal before attempting a task-token callback."""
import json
import logging
import boto3
from common import dynamo as d
from common.models import validate_event
from common.catalogo import LEGACY


def key(pid):return {'espera_id':'RECARGA#'+pid}


def send_success(token,paso_id):
    sfn=boto3.client('stepfunctions')
    try:
        sfn.send_task_success(taskToken=token,output=json.dumps({'estado':'COBRADO','paso_id':paso_id}))
    except (sfn.exceptions.TaskDoesNotExist,sfn.exceptions.InvalidToken):
        if d.get(d.client(),'PASOS',{'paso_id':paso_id})['estado']!='COBRADO':raise


def completar(paso_id):
    db=d.client();paso=d.get(db,'PASOS',{'paso_id':paso_id})
    if not paso or paso['estado']!='COBRADO':return 'SIN_CAMBIO'
    old=d.get(db,'ESPERAS',key(paso_id))
    if not old:
        try:
            db.put_item(TableName=d.table('ESPERAS'),Item=d.encode(dict(key(paso_id),paso_id=paso_id,
                tipo='RECARGA',cerrar=True)),**d.guard(None,['espera_id']))
        except Exception as exc:
            if getattr(exc,'response',{}).get('Error',{}).get('Code')!='ConditionalCheckFailedException':raise
        old=d.get(db,'ESPERAS',key(paso_id))
    token=old.get('token') if old else None
    if not token:return 'SENAL_GUARDADA'
    send_success(token,paso_id)
    try:
        db.delete_item(TableName=d.table('ESPERAS'),Key=d.encode(key(paso_id)),
            **d.guard(old,['token']))
    except Exception as exc:
        if getattr(exc,'response',{}).get('Error',{}).get('Code')!='ConditionalCheckFailedException':raise
    return 'COMPLETADO'


def handler(event,context):
    failed=[]
    for record in event['Records']:
        try:
            msg=json.loads(record['body']);validate_event(msg)
            if LEGACY.get(msg['detail-type'],msg['detail-type'])!='paso.cobrado':raise ValueError('UNSUPPORTED_CALLBACK')
            completar(msg['detail']['paso_id'])
        except Exception as exc:
            logging.error('wait callback failed message_id=%s error_type=%s',record['messageId'],type(exc).__name__)
            failed.append({'itemIdentifier':record['messageId']})
    return {'batchItemFailures':failed}
