"""Persist pass and outbox together; never wait for billing."""
import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeSerializer
from common.models import paso_id, make_event, utc_timestamp
from common.events import outbox_item
from common.observability import emit
from common.http import response

_ddb = None

class TollNotFound(Exception):
    pass

def database():
    global _ddb
    if _ddb is None:
        _ddb = boto3.client('dynamodb')
    return _ddb

def encode(item):
    serializer = TypeSerializer()
    return {k: serializer.serialize(v) for k, v in item.items()}

def normalize(aviso):
    if not isinstance(aviso, dict) or set(aviso) - {'placa', 'peaje_id', 'timestamp', 'tag_id'}:
        raise ValueError('Campos inválidos.')
    placa, peaje, timestamp = (aviso.get(k) for k in ('placa', 'peaje_id', 'timestamp'))
    if not isinstance(placa, str) or not re.fullmatch(r'[A-Z]-?\d{3}[A-Z]{3}', placa.strip().upper()):
        raise ValueError('Placa inválida.')
    if not isinstance(peaje, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', peaje.strip()):
        raise ValueError('Peaje inválido.')
    if not isinstance(timestamp, str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', timestamp):
        raise ValueError('Timestamp UTC requerido, máximo seis decimales.')
    tag = aviso.get('tag_id')
    if tag is not None and (not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', tag)):
        raise ValueError('Tag inválido.')
    return {'placa': placa.strip().upper(), 'peaje_id': peaje.strip(),
            'timestamp': utc_timestamp(timestamp), 'tag_id': tag}

def registrar_paso(aviso: dict) -> dict:
    return _register(aviso, str(uuid4()))

def _register(aviso, request_id):
    value = normalize(aviso)
    identifier = paso_id(value['placa'], value['peaje_id'], value['timestamp'])
    db = database()
    key = encode({'paso_id': identifier})
    if db.get_item(TableName=os.environ['PASOS_TABLE'], Key=key, ConsistentRead=True).get('Item'):
        emit(paso_id=identifier, request_id=request_id, estado='DUPLICADO')
        return {'paso_id': identifier}
    received = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    local_date = datetime.fromisoformat(value['timestamp'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Guatemala')).date().isoformat()
    item = dict(value, paso_id=identifier, recibido_en=received, fecha_local=local_date,
                estado='REGISTRADO', version=1, modalidad=None, monto_centavos=None,
                peaje_fecha=value['peaje_id']+'#'+local_date, orden=value['timestamp']+'#'+identifier)
    event = make_event('paso.registrado', identifier, 1,
                       dict(value, paso_id=identifier, recibido_en=received, fecha_local=local_date),
                       request_id, received, os.environ['OWNER_CARNET'])
    try:
        db.transact_write_items(TransactItems=[
            {'ConditionCheck': {'TableName': os.environ['PEAJES_TABLE'],
                'Key': encode({'peaje_id': value['peaje_id']}),
                'ConditionExpression': 'attribute_exists(peaje_id)'}},
            {'Put': {'TableName': os.environ['PASOS_TABLE'], 'Item': encode(item),
                     'ConditionExpression': 'attribute_not_exists(paso_id)'}},
            {'Put': {'TableName': os.environ['OUTBOX_TABLE'], 'Item': encode(outbox_item(event)),
                     'ConditionExpression': 'attribute_not_exists(evento_id)'}},
        ])
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'TransactionCanceledException':
            raise
        if db.get_item(TableName=os.environ['PASOS_TABLE'], Key=key, ConsistentRead=True).get('Item'):
            emit(paso_id=identifier, request_id=request_id, estado='DUPLICADO')
            return {'paso_id': identifier}
        reasons = exc.response.get('CancellationReasons', [])
        if reasons and reasons[0].get('Code') == 'ConditionalCheckFailed':
            raise TollNotFound('TOLL_NOT_FOUND') from exc
        raise
    emit(paso_id=identifier, evento_id=event['detail']['evento_id'],
         request_id=request_id, estado='REGISTRADO')
    return {'paso_id': identifier}

def handler(event, context):
    try:
        body = event.get('body') or ''
        if event.get('isBase64Encoded'):
            body = base64.b64decode(body, validate=True).decode('utf-8')
        result = _register(json.loads(body), event.get('requestContext', {}).get('requestId') or str(uuid4()))
        return response(event, 202, result, message='Paso recibido.')
    except (ValueError, TypeError, UnicodeError):
        emit(request_id=event.get('requestContext', {}).get('requestId'), error='VALIDATION_ERROR')
        return response(event, 400, message='Aviso inválido.', code='VALIDATION_ERROR')
    except TollNotFound:
        emit(request_id=event.get('requestContext', {}).get('requestId'), error='TOLL_NOT_FOUND')
        return response(event, 404, message='Peaje inexistente.', code='TOLL_NOT_FOUND')
    except Exception:
        emit(request_id=event.get('requestContext', {}).get('requestId'), error='PERSISTENCE_ERROR')
        logging.exception('webhook persistence failed')
        return response(event, 503, message='Reintente el mismo aviso.', code='TEMPORARILY_UNAVAILABLE')
