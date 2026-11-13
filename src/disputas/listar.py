"""Role-scoped, cursor-paginated dispute history."""
import base64
import json
import os
import boto3
from boto3.dynamodb.conditions import Key


def listar_disputas(actor: dict, limit: int = 20, cursor: str | None = None) -> dict:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('INVALID_LIMIT')
    if actor.get('role') == 'conductor':
        scope, index, field = actor['placa'], 'PorPlaca', 'placa'
    elif actor.get('role') == 'atencion':
        scope, index, field = 'ABIERTA', 'PorEstado', 'estado'
    else:
        raise PermissionError('FORBIDDEN')
    params = {'IndexName': index, 'KeyConditionExpression': Key(field).eq(scope),
              'Limit': limit, 'ScanIndexForward': False}
    if cursor:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError('INVALID_CURSOR')
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if data['scope'] != scope or data['index'] != index:
                raise ValueError('INVALID_CURSOR')
            params['ExclusiveStartKey'] = data['key']
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError('INVALID_CURSOR') from exc
    page = boto3.resource('dynamodb').Table(os.environ['DISPUTAS_TABLE']).query(**params)
    items = [{k: row[k] for k in ('disputa_id', 'cobro_id', 'estado', 'motivo', 'vence_en', 'devolucion_id') if k in row}
             for row in page.get('Items', []) if row.get('disputa_id')]
    last = page.get('LastEvaluatedKey')
    token = base64.urlsafe_b64encode(json.dumps({'scope': scope, 'index': index, 'key': last}).encode()).decode().rstrip('=') if last else None
    return {'items': items, 'next_cursor': token}
