import logging, os
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
        # Task 3 minimum read probe; pagination cursor arrives with portal integration.
        if set(q)-{'fecha','limit'}: return response(event,400,message='Parámetro no soportado en esta base.',code='VALIDATION_ERROR')
        limit=int(q.get('limit',50))
        if not 1<=limit<=100: raise ValueError()
        result=boto3.resource('dynamodb').Table(os.environ['PASOS_TABLE']).query(IndexName='PorPeajeFecha',KeyConditionExpression=Key('peaje_fecha').eq(toll+'#'+fecha),Limit=limit,ScanIndexForward=False)
        if result.get('LastEvaluatedKey'):
            return response(event,501,message='Paginación pendiente de implementación; no se devuelve una lista incompleta.',code='NOT_IMPLEMENTED')
        items=[]
        for row in result.get('Items',[]):
            amount=row.get('monto_centavos');amount=None if amount is None else int(amount)
            items.append({'paso_id':row['paso_id'],'placa':row['placa'],'timestamp':row['timestamp'],'estado':row['estado'],'monto':None if amount is None else f'{amount//100}.{amount%100:02d}'})
        return response(event,200,{'fecha':fecha,'peaje_id':toll,'items':items,'next_cursor':None})
    except AuthError as exc:return response(event,exc.status,message=str(exc),code='FORBIDDEN' if exc.status==403 else 'UNAUTHORIZED')
    except (ValueError,TypeError):return response(event,400,message='Fecha o límite inválido.',code='VALIDATION_ERROR')
    except Exception:
        logging.exception('passes request_id=%s',context.aws_request_id)
        return response(event,500,message='Error interno.',code='INTERNAL_ERROR')
