"""Paginated read projections. Wallet decisions use base-table consistent reads."""
import base64
import json
import logging
import os
import boto3
from boto3.dynamodb.conditions import Key
from common.auth import AuthError, require_owner
from common.http import response


def cursor_encode(key, placa, scope):
    if not key:return None
    data={'placa':placa,'scope':scope,'key':key}
    return base64.urlsafe_b64encode(json.dumps(data,separators=(',',':')).encode()).decode().rstrip('=')


def cursor_decode(value,placa,scope):
    if not isinstance(value,str) or not value or len(value)>2048:raise ValueError('INVALID_CURSOR')
    try:
        data=json.loads(base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True))
        key=data['key']
        if data['placa']!=placa or data['scope']!=scope or not isinstance(key,dict) or key.get('placa')!=placa:
            raise ValueError('INVALID_CURSOR')
        if len(key)>4 or any(not isinstance(k,str) or not isinstance(v,str) or len(v)>256 for k,v in key.items()):
            raise ValueError('INVALID_CURSOR')
        return key
    except (ValueError,KeyError,TypeError,UnicodeError) as exc:
        raise ValueError('INVALID_CURSOR') from exc


def money(value):
    cents=int(value)
    return f'{cents//100}.{cents%100:02d}'


def handler(event,context):
    try:
        placa=(event.get('pathParameters') or {}).get('placa')
        claims=require_owner(event,placa,os.environ['CLIENT_ID'])
        path=event.get('path') or event.get('resource') or ''
        if path.startswith('/history/payments/'):
            scope='payments';name='MOVIMIENTOS';index='PorPlaca'
        elif path.startswith('/history/invoices/'):
            scope='invoices';name='FACTURAS';index='PorPlaca'
        elif path.startswith('/users/') and path.endswith('/passes'):
            scope='passes';name='PASOS';index='PorPlaca'
        else:raise ValueError('INVALID_PATH')
        q=event.get('queryStringParameters') or {}
        if set(q)-{'limit','cursor'}:raise ValueError('INVALID_QUERY')
        limit=int(q.get('limit','20'))
        if not 1<=limit<=100:raise ValueError('INVALID_LIMIT')
        kwargs={'IndexName':index,'KeyConditionExpression':Key('placa').eq(placa),
            'ScanIndexForward':False,'Limit':limit}
        if q.get('cursor'):kwargs['ExclusiveStartKey']=cursor_decode(q['cursor'],placa,scope)
        page=boto3.resource('dynamodb').Table(os.environ[name+'_TABLE']).query(**kwargs)
        items=[]
        for row in page.get('Items',[]):
            if scope=='payments':
                if row.get('tipo')!='COBRO':continue
                items.append({'cobro_id':row['cobro_id'],'paso_id':row['paso_id'],
                    'peaje_id':row['peaje_id'],'monto':money(row['monto_centavos']),
                    'moneda':'GTQ','creado_en':row['fecha'],'devuelto':False})
            elif scope=='invoices':
                items.append({'factura_id':row['factura_id'],'paso_id':row['paso_id'],
                    'numero':row['numero'],'fecha':row['fecha'],
                    'monto':money(row['monto_centavos']),'concepto':row['concepto'],
                    'estado':row.get('estado','POR_COBRAR'),'contribuyente':
                    {'nombre':row.get('nombre',''),'nit':row.get('NIT') or row.get('nit','')}})
            else:
                amount=row.get('monto_centavos')
                items.append({'paso_id':row['paso_id'],'peaje_id':row['peaje_id'],
                    'fecha_local':row.get('fecha_local'),'estado':row['estado'],
                    'recibido_en':row['recibido_en'],'vence_en':row.get('vence_en'),
                    'monto':money(amount) if amount is not None else None,
                    'version':int(row.get('version',0))})
        return response(event,200,{'items':items,'next_cursor':cursor_encode(page.get('LastEvaluatedKey'),placa,scope)})
    except AuthError as exc:
        return response(event,exc.status,message=str(exc),code='UNAUTHORIZED' if exc.status==401 else 'FORBIDDEN')
    except (ValueError,TypeError,KeyError):
        return response(event,400,message='Consulta inválida.',code='VALIDATION_ERROR')
    except Exception as exc:
        logging.error('history failed error_type=%s',type(exc).__name__)
        return response(event,503,message='Reintente la consulta.',code='TEMPORARILY_UNAVAILABLE')
