import logging, os, base64, json
from datetime import datetime, date
from zoneinfo import ZoneInfo
import boto3
from boto3.dynamodb.conditions import Key
from common.auth import AuthError, operator_toll
from common.http import response

def handler(event,context):
    try:
        toll=operator_toll(event,os.environ['CLIENT_ID'])
        q=event.get('queryStringParameters') or {}
        fecha=q.get('fecha') or datetime.now(ZoneInfo('America/Guatemala')).date().isoformat()
        if date.fromisoformat(fecha).isoformat()!=fecha: raise ValueError()
        if 'peaje_id' in q: return response(event,403,message='El peaje se obtiene de la sesión.',code='FORBIDDEN')
        if set(q)-{'fecha','limit','cursor'}: return response(event,400,message='Parámetro no soportado.',code='VALIDATION_ERROR')
        limit=int(q.get('limit',50))
        if not 1<=limit<=100: raise ValueError()
        arguments={'IndexName':'PorPeajeFecha','KeyConditionExpression':Key('peaje_fecha').eq(toll+'#'+fecha),
            'Limit':limit,'ScanIndexForward':False}
        if q.get('cursor'):
            token=q['cursor']
            if len(token)>2048:raise ValueError('INVALID_CURSOR')
            cursor=json.loads(base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True))
            start=cursor.get('key');scope=toll+'#'+fecha
            if cursor.get('scope')!=scope or not isinstance(start,dict) or start.get('peaje_fecha')!=scope:
                raise ValueError('INVALID_CURSOR')
            if len(start)>4 or any(not isinstance(k,str) or not isinstance(v,str) for k,v in start.items()):
                raise ValueError('INVALID_CURSOR')
            arguments['ExclusiveStartKey']=start
        result=boto3.resource('dynamodb').Table(os.environ['PASOS_TABLE']).query(**arguments)
        items=[]
        for row in result.get('Items',[]):
            amount=row.get('monto_centavos');amount=None if amount is None else int(amount)
            items.append({'paso_id':row['paso_id'],'placa':row['placa'],'timestamp':row['timestamp'],'estado':row['estado'],'monto':None if amount is None else f'{amount//100}.{amount%100:02d}', 'version':int(row.get('version',0)), 'fecha_local':row.get('fecha_local'),'vence_en':row.get('vence_en'),'peaje_id':toll})
        key=result.get('LastEvaluatedKey')
        next_cursor=base64.urlsafe_b64encode(json.dumps({'scope':toll+'#'+fecha,'key':key},separators=(',',':')).encode()).decode().rstrip('=') if key else None
        return response(event,200,{'fecha':fecha,'peaje_id':toll,'items':items,'next_cursor':next_cursor})
    except AuthError as exc:return response(event,exc.status,message=str(exc),code='FORBIDDEN' if exc.status==403 else 'UNAUTHORIZED')
    except (ValueError,TypeError,KeyError,UnicodeError):return response(event,400,message='Fecha o límite inválido.',code='VALIDATION_ERROR')
    except Exception:
        logging.exception('passes request_id=%s',context.aws_request_id)
        return response(event,500,message='Error interno.',code='INTERNAL_ERROR')
