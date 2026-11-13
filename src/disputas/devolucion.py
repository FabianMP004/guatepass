"""Finalize a durable dispute decision with one conditional DynamoDB transaction.

The API/timeout must persist `decision` before calling this function. The
dispute's primary key is cobro_id; disputa_id is stable_id('disputa', cobro_id).
"""
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from common import dynamo as d
from common.events import outbox_item
from common.models import make_event, stable_id


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _event(kind, row, version, payload, at, carnet):
    return d.put('OUTBOX', outbox_item(make_event(
        kind, row['disputa_id'], version, payload,
        'disputa:' + row['disputa_id'], at, carnet)))


def _load(db, disputa_id):
    if not isinstance(disputa_id, str) or not disputa_id.startswith('disputa_'):
        raise ValueError('INVALID_DISPUTA_ID')
    # A direct lookup by cobro_id needs the caller to provide it or an index;
    # derive it from a stored mapping is impossible for a SHA-256 dispute id.
    # Query the existing PorEstado index is insufficient for closed disputes.
    # Use an explicitly stored reference item keyed by the dispute id.
    ref = d.get(db, 'DISPUTAS', {'cobro_id': 'REF#' + disputa_id})
    if not ref:
        raise ValueError('DISPUTE_NOT_FOUND')
    row = d.get(db, 'DISPUTAS', {'cobro_id': ref['target_cobro_id']})
    if not row or row.get('disputa_id') != disputa_id:
        raise ValueError('DISPUTE_NOT_FOUND')
    return row


def aplicar_devolucion(disputa_id: str) -> dict:
    """Idempotently apply the recorded approval or rejection, never choose it."""
    db = d.client()
    for attempt in range(6):
        row = _load(db, disputa_id)
        if row['estado'] in ('APROBADA', 'RECHAZADA'):
            return {'disputa_id': disputa_id, 'estado': row['estado'],
                    'devolucion_id': row.get('devolucion_id')}
        if row['estado'] != 'ABIERTA' or row.get('decision') not in ('APROBAR', 'RECHAZAR'):
            raise ValueError('DISPUTE_DECISION_REQUIRED')
        if type(row.get('version')) is not int or row['version'] < 1:
            raise ValueError('INVALID_DISPUTE_VERSION')
        amount = row.get('monto_centavos')
        if type(amount) is not int or amount <= 0:
            raise ValueError('INVALID_REFUND_AMOUNT')
        at = _stamp()
        approval = row['decision'] == 'APROBAR'
        carnet = row.get('carnet_decisor') or os.environ['OWNER_CARNET']
        reason = row.get('motivo_decision') or ('Vencimiento automático' if row.get('resolucion') == 'AUTOMATICA' else '')
        if not reason.strip():
            raise ValueError('MISSING_DECISION_REASON')
        version = row['version'] + 1
        refund_id = stable_id('devolucion', disputa_id)
        final = dict(row, estado='APROBADA' if approval else 'RECHAZADA',
                     version=version, resuelta_en=at)
        if approval:
            final['devolucion_id'] = refund_id
        payload = {'disputa_id': disputa_id, 'cobro_id': row['cobro_id'],
                   'placa': row['placa'], 'decision': row['decision'], 'motivo': reason}
        tx = [d.put('DISPUTAS', final, row, ['version', 'estado', 'decision']),
              _event('disputa.resuelta', row, version, payload, at, carnet)]
        if approval:
            user = d.get(db, 'USUARIOS', {'placa': row['placa']})
            if not user or user.get('sub') != row.get('propietario_sub') or not row.get('propietario_sub'):
                raise ValueError('DISPUTE_OWNER_MISMATCH')
            balance = user.get('saldo_centavos')
            if type(balance) is not int or balance < 0 or type(user.get('version')) is not int:
                raise ValueError('INVALID_WALLET')
            local_date = datetime.fromisoformat(at.replace('Z', '+00:00')).astimezone(ZoneInfo('America/Guatemala')).date().isoformat()
            movement = {'movimiento_id': refund_id, 'devolucion_id': refund_id,
                        'disputa_id': disputa_id, 'cobro_id': row['cobro_id'],
                        'paso_id': row.get('paso_id'),
                        'tipo': 'DEVOLUCION', 'placa': row['placa'],
                        'peaje_id': row['peaje_id'], 'monto_centavos': amount,
                        'saldo_anterior_centavos': balance, 'saldo_centavos': balance + amount,
                        'fecha': at, 'fecha_local': local_date,
                        'peaje_fecha': row['peaje_id'] + '#' + local_date,
                        'orden': at + '#' + refund_id}
            update = {'Update': {'TableName': d.table('USUARIOS'),
                'Key': d.encode({'placa': row['placa']}),
                'ConditionExpression': '#v = :version AND #s = :sub AND #b = :balance',
                'UpdateExpression': 'SET #v = :next, #b = #b + :amount',
                'ExpressionAttributeNames': {'#v': 'version', '#s': 'sub', '#b': 'saldo_centavos'},
                'ExpressionAttributeValues': d.encode({':version': user['version'],
                    ':next': user['version'] + 1, ':sub': user['sub'],
                    ':balance': balance, ':amount': amount})}}
            refund_event = {'disputa_id': disputa_id, 'devolucion_id': refund_id,
                'cobro_id': row['cobro_id'], 'placa': row['placa'],
                'peaje_id': row['peaje_id'], 'devuelto_en': at,
                'resolucion': row.get('resolucion', 'MANUAL'),
                'monto_centavos': amount, 'saldo_centavos': balance + amount}
            tx.extend([update, d.put('MOVIMIENTOS', movement),
                       _event('saldo.devuelto', row, version, refund_event, at, carnet)])
        try:
            db.transact_write_items(TransactItems=tx)
            return {'disputa_id': disputa_id, 'estado': final['estado'],
                    'devolucion_id': refund_id if approval else None}
        except Exception as exc:
            # A retry after an ambiguous commit reads the terminal dispute.
            if not d.retryable(exc) or attempt == 5:
                raise
            d.backoff(attempt)
    raise RuntimeError('DISPUTE_RETRY_EXHAUSTED')
