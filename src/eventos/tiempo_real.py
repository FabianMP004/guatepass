"""Publish a stored transition snapshot to the operator channel using Lambda IAM."""
import json
import logging
import os
import re
from urllib.request import Request, urlopen
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from common.models import validate_event
from common.catalogo import LEGACY

KINDS={'paso.registrado','paso.cobrado','paso.pendiente','paso.no_registrado',
       'paso.no_identificado','paso.facturado'}

def channel_for(toll):
    if not isinstance(toll,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,22}',toll):
        raise ValueError('INVALID_TOLL_CHANNEL')
    suffix='p-'+toll.replace('-', '-0').replace('_', '-1')
    return '/peajes/'+suffix

def projection(event):
    validate_event(event)
    detail=event['detail'];kind=LEGACY.get(event['detail-type'],event['detail-type'])
    if kind not in KINDS:raise ValueError('NOT_A_PASS_EVENT')
    if not isinstance(detail.get('peaje_id'),str):raise ValueError('MISSING_TOLL')
    stamp=detail['occurred_at']
    from zoneinfo import ZoneInfo
    from datetime import datetime
    local=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(ZoneInfo('America/Guatemala')).date().isoformat()
    return {'paso_id':detail['paso_id'],'peaje_id':detail['peaje_id'],
        'placa':detail['placa'],'estado':{'paso.registrado':'REGISTRADO','paso.cobrado':'COBRADO',
        'paso.pendiente':'PENDIENTE','paso.no_registrado':'NO_REGISTRADO',
        'paso.no_identificado':'NO_IDENTIFICADO','paso.facturado':'FACTURADO'}[kind],
        'version':detail['aggregate_version'],'fecha_local':detail.get('fecha_local') or local,
        'evento_id':detail['evento_id'],'monto_centavos':detail.get('monto_centavos'),
        'vence_en':detail.get('vence_en')}

def publicar(event):
    data=projection(event)
    domain=os.environ['REALTIME_HTTP_DOMAIN'];region=os.environ['AWS_REGION']
    url='https://'+domain+'/event';body=json.dumps({'channel':channel_for(data['peaje_id']),
        'events':[json.dumps(data,ensure_ascii=False,separators=(',',':'))]},separators=(',',':')).encode()
    headers={'Content-Type':'application/json','Host':domain}
    request=AWSRequest(method='POST',url=url,data=body,headers=headers)
    credentials=boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials,'appsync',region).add_auth(request)
    with urlopen(Request(url,data=body,headers=dict(request.headers),method='POST'),timeout=8) as res:
        reply=json.load(res)
    if len(reply.get('successful',[]))!=1 or reply.get('failed'):
        raise RuntimeError('REALTIME_PARTIAL_FAILURE')
    return data['evento_id']

def handler(event,context):
    failed=[]
    for record in event['Records']:
        try:publicar(json.loads(record['body']))
        except Exception as exc:
            logging.error('realtime failed message_id=%s error_type=%s',record['messageId'],type(exc).__name__)
            failed.append({'itemIdentifier':record['messageId']})
    return {'batchItemFailures':failed}
