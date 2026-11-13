"""Daily Guatemala toll revenue. Small-demo tables are read consistently, page by page."""
import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo

import boto3

from common import dynamo as d
from common.auth import AuthError, require_role
from common.events import outbox_item
from common.http import response
from common.models import make_event, stable_id

GUATEMALA = ZoneInfo('America/Guatemala')
KINDS = {'COBRO', 'DEVOLUCION'}


def local_date(value):
    if not isinstance(value, str):
        raise ValueError('INVALID_DATE')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('INVALID_DATE') from exc
    if parsed.isoformat() != value:
        raise ValueError('INVALID_DATE')
    return parsed


def yesterday():
    return (datetime.now(GUATEMALA).date() - timedelta(days=1)).isoformat()


def utc_bounds(fecha_local):
    day = local_date(fecha_local)
    start = datetime.combine(day, time.min, GUATEMALA).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, GUATEMALA).astimezone(timezone.utc)
    return start, end


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError('INVALID_TIMESTAMP')
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('INVALID_TIMESTAMP') from exc
    if moment.tzinfo is None:
        raise ValueError('INVALID_TIMESTAMP')
    return moment.astimezone(timezone.utc)


def _pages(db, name):
    start = None
    while True:
        kwargs = {'TableName': d.table(name), 'ConsistentRead': True}
        if start:
            kwargs['ExclusiveStartKey'] = start
        page = db.scan(**kwargs)
        for item in page.get('Items', []):
            yield d.decode(item)
        start = page.get('LastEvaluatedKey')
        if not start:
            break


def _amount(row):
    value = row.get('monto_centavos')
    if type(value) is not int or value <= 0:
        raise ValueError('INVALID_REVENUE_AMOUNT')
    return value


def _empty(peaje_id):
    return {'peaje_id': peaje_id, 'cobros_centavos': 0,
            'devoluciones_centavos': 0, 'neto_centavos': 0,
            'por_cobrar_centavos': 0}


def _peajes(db):
    return {row['peaje_id']: row for row in _pages(db, 'PEAJES')}


def _save(db, fecha, item):
    identifier = stable_id('recaudacion', fecha, item['peaje_id'])
    content = {'fecha': fecha, **item}
    digest = sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    for attempt in range(6):
        previous = d.get(db, 'RECAUDACION', {'recaudacion_id': identifier})
        if previous and previous.get('contenido_hash') == digest:
            return previous
        version = previous['version'] + 1 if previous else 1
        at = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
        record = {'recaudacion_id': identifier, **content, 'contenido_hash': digest,
                  'version': version, 'calculado_en': at}
        event = make_event('recaudacion.calculada', identifier, version,
            {'reporte_id': identifier, 'fecha': fecha, 'peaje_id': item['peaje_id'],
             'calculado_en': at, **{key: item[key] for key in (
                 'cobros_centavos', 'devoluciones_centavos', 'neto_centavos', 'por_cobrar_centavos')}},
            'recaudacion:' + identifier, at, os.environ['OWNER_CARNET'])
        try:
            db.transact_write_items(TransactItems=[
                d.put('RECAUDACION', record, previous, ['version']),
                d.put('OUTBOX', outbox_item(event))])
            return record
        except Exception as exc:
            if not d.retryable(exc) or attempt == 5:
                raise
            d.backoff(attempt)
    raise RuntimeError('REVENUE_RETRY_EXHAUSTED')


def _totals(items):
    return {key: sum(int(item[key]) for item in items) for key in
            ('cobros_centavos', 'devoluciones_centavos', 'neto_centavos', 'por_cobrar_centavos')}


def calcular_recaudacion(fecha_local: str) -> dict:
    """Recompute each configured toll; reruns with equal content create no new event."""
    start, end = utc_bounds(fecha_local)
    db = d.client()
    peajes = _peajes(db)
    items = {key: _empty(key) for key in peajes}
    for row in _pages(db, 'MOVIMIENTOS'):
        if row.get('tipo') not in KINDS or not start <= _stamp(row.get('fecha')) < end:
            continue
        peaje = row['peaje_id']
        if peaje not in items:
            raise ValueError('UNKNOWN_REVENUE_TOLL')
        amount = _amount(row)
        if row['tipo'] == 'COBRO':
            items[peaje]['cobros_centavos'] += amount
        else:
            items[peaje]['devoluciones_centavos'] += amount
    for row in _pages(db, 'FACTURAS'):
        if not start <= _stamp(row.get('emitida_en')) < end:
            continue
        peaje = row['peaje_id']
        if peaje not in items:
            raise ValueError('UNKNOWN_REVENUE_TOLL')
        items[peaje]['por_cobrar_centavos'] += _amount(row)
    for item in items.values():
        item['neto_centavos'] = item['cobros_centavos'] - item['devoluciones_centavos']
    saved = [_save(db, fecha_local, items[key]) for key in sorted(items)]
    return {'fecha': fecha_local, 'desde_utc': start.isoformat().replace('+00:00', 'Z'),
            'hasta_utc_exclusivo': end.isoformat().replace('+00:00', 'Z'),
            'peajes': saved, 'totales': _totals(saved)}


def obtener_recaudacion(fecha_local):
    start, end = utc_bounds(fecha_local)
    db = d.client()
    records = [d.get(db, 'RECAUDACION',
               {'recaudacion_id': stable_id('recaudacion', fecha_local, key)})
               for key in sorted(_peajes(db))]
    records = [row for row in records if row]
    return {'fecha': fecha_local, 'desde_utc': start.isoformat().replace('+00:00', 'Z'),
            'hasta_utc_exclusivo': end.isoformat().replace('+00:00', 'Z'),
            'peajes': records, 'totales': _totals(records), 'calculado': bool(records)}


def handler(event, context):
    event = event or {}
    if 'httpMethod' not in event:
        return calcular_recaudacion(event.get('fecha') or yesterday())
    try:
        if event['httpMethod'] != 'GET':
            return response(event, 405, code='METHOD_NOT_ALLOWED', message='Método no permitido.')
        require_role(event, 'administrador', os.environ['CLIENT_ID'])
        if event.get('queryStringParameters'):
            raise ValueError('INVALID_QUERY')
        resource = event.get('resource') or event.get('path') or ''
        if resource == '/tolls':
            peajes = _peajes(d.client())
            return response(event, 200, {'items': [
                {k: row[k] for k in ('peaje_id', 'tarifa_base_centavos', 'tarifa_premium_centavos', 'activo') if k in row}
                for _, row in sorted(peajes.items())]})
        if resource in ('/revenue', '/revenue/{fecha}'):
            fecha = ((event.get('pathParameters') or {}).get('fecha')
                     if resource == '/revenue/{fecha}' else yesterday())
            return response(event, 200, obtener_recaudacion(fecha))
        raise ValueError('INVALID_PATH')
    except AuthError as exc:
        return response(event, exc.status, code='UNAUTHORIZED' if exc.status == 401 else 'FORBIDDEN',
                        message=str(exc))
    except (ValueError, TypeError):
        return response(event, 400, code='VALIDATION_ERROR', message='Consulta inválida.')
    except Exception as exc:
        logging.error('revenue API failed error_type=%s', type(exc).__name__)
        return response(event, 503, code='TEMPORARILY_UNAVAILABLE', message='Reintente la consulta.')
