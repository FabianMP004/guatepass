import logging, os
import boto3
from common.auth import AuthError, require_owner
from common.http import response

def handler(event,context):
    try:
        placa=(event.get('pathParameters') or {}).get('placa')
        claims=require_owner(event,placa,os.environ['CLIENT_ID'])
        item=boto3.resource('dynamodb').Table(os.environ['USUARIOS_TABLE']).get_item(Key={'placa':placa},ConsistentRead=True).get('Item')
        if not item: return response(event,404,message='Usuario no encontrado.',code='USER_NOT_FOUND')
        if item.get('sub')!=claims['sub']: raise AuthError()
        balance=int(item['saldo_centavos'])
        return response(event,200,{'placa':placa,'saldo':f'{balance//100}.{balance%100:02d}','moneda':'GTQ'})
    except AuthError as exc: return response(event,exc.status,message=str(exc),code='FORBIDDEN' if exc.status==403 else 'UNAUTHORIZED')
    except Exception:
        logging.exception('users request_id=%s',context.aws_request_id)
        return response(event,500,message='Error interno.',code='INTERNAL_ERROR')
