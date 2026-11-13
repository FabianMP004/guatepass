"""Open one dispute per paid movement using a conditional transaction."""
import os
from datetime import datetime, timezone
import boto3
from common import dynamo as d
from common.deadlines import deadline
from common.events import outbox_item
from common.models import make_event, stable_id


class DisputeConflict(ValueError):
    pass


def abrir_disputa(cobro_id: str, motivo: str, actor: dict) -> dict:
    if not isinstance(cobro_id, str) or not cobro_id.startswith('cobro_'):
        raise ValueError('INVALID_PAYMENT')
    if not isinstance(motivo, str) or not 1 <= len(motivo.strip()) <= 1000:
        raise ValueError('INVALID_REASON')
    if not isinstance(actor, dict) or not actor.get('sub') or not actor.get('placa'):
        raise ValueError('INVALID_ACTOR')
    db = d.client()
    payment = d.get(db, 'MOVIMIENTOS', {'movimiento_id': cobro_id})
    if not payment or payment.get('tipo') != 'COBRO':
        raise ValueError('PAYMENT_NOT_FOUND')
    if payment['placa'] != actor['placa']:
        raise PermissionError('FORBIDDEN')
    user = d.get(db, 'USUARIOS', {'placa': payment['placa']})
    if not user or user.get('sub') != actor['sub'] or not user.get('carnet'):
        raise PermissionError('FORBIDDEN')
    if d.get(db, 'DISPUTAS', {'cobro_id': cobro_id}):
        raise DisputeConflict('DISPUTE_EXISTS')
    at = datetime.now(timezone.utc)
    issued = at.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    due = deadline(boto3.client('ssm'), os.environ['DISPUTA_TIMEOUT_PARAMETER'], at)
    did = stable_id('disputa', cobro_id)
    row = {'cobro_id': cobro_id, 'disputa_id': did, 'placa': payment['placa'],
           'peaje_id': payment['peaje_id'], 'propietario_sub': user['sub'],
           'paso_id': payment.get('paso_id'),
           'monto_centavos': int(payment['monto_centavos']), 'motivo': motivo.strip(),
           'estado': 'ABIERTA', 'version': 1, 'vence_en': due,
           'orden': issued + '#' + did, 'abierta_en': issued}
    event = make_event('disputa.abierta', did, 1,
        {'disputa_id': did, 'cobro_id': cobro_id, 'placa': payment['placa'], 'vence_en': due},
        actor.get('request_id') or 'disputa:' + did, issued, user['carnet'])
    try:
        db.transact_write_items(TransactItems=[
            d.check('MOVIMIENTOS', {'movimiento_id': cobro_id}, payment,
                    ['movimiento_id', 'tipo', 'placa', 'monto_centavos']),
            d.check('USUARIOS', {'placa': payment['placa']}, user, ['sub', 'carnet']),
            d.put('DISPUTAS', row),
            d.put('DISPUTAS', {'cobro_id': 'REF#' + did, 'target_cobro_id': cobro_id}),
            d.put('OUTBOX', outbox_item(event))])
    except Exception:
        if d.get(db, 'DISPUTAS', {'cobro_id': cobro_id}):
            raise DisputeConflict('DISPUTE_EXISTS')
        raise
    return {'disputa_id': did, 'cobro_id': cobro_id, 'estado': 'ABIERTA', 'vence_en': due}
