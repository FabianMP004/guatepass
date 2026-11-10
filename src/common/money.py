"""All production wallet writes belong here; money is integral GTQ centavos.

Classification is committed first. A crash thereafter must reuse its snapshot.
Every financial transition commits wallet, pass, movement/pending and Outbox
together. No network call to an external registry is made here.
"""
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from common import dynamo as d
from common.deadlines import deadline
from common.events import outbox_item
from common.models import make_event, stable_id
import hashlib
import json

MAX_ATTEMPTS = 8
TERMINAL = {'COBRADO', 'NO_REGISTRADO', 'FACTURADO', 'NO_IDENTIFICADO'}
MAX_TOPUP_CENTAVOS = 1_000_000  # Q10,000.00 per operation


def format_centavos(value):
    return f'{value // 100}.{value % 100:02d}'


def topup_id(placa, clave):
    return stable_id('idempotencia', placa, 'RECARGA', clave)


def apply_topup(placa: str, monto_centavos: int, clave: str, actor: dict) -> dict:
    if type(monto_centavos) is not int or not 1 <= monto_centavos <= MAX_TOPUP_CENTAVOS:
        raise ValueError('INVALID_AMOUNT')
    if not isinstance(placa, str) or not placa or not isinstance(clave, str) or not 1 <= len(clave) <= 128 or not clave.isascii() or any(c.isspace() for c in clave):
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    if not isinstance(actor, dict) or actor.get('custom:placa') != placa or not actor.get('sub'):
        raise ValueError('INVALID_ACTOR')
    identifier = topup_id(placa, clave)
    recarga_id = stable_id('recarga', placa, clave)
    digest = hashlib.sha256(json.dumps({'monto_centavos': monto_centavos},
        separators=(',', ':'), sort_keys=True).encode()).hexdigest()
    db = d.client()
    for attempt in range(MAX_ATTEMPTS):
        user = d.get(db, 'USUARIOS', {'placa': placa})
        if not user or user.get('sub') != actor['sub']:
            raise ValueError('INVALID_ACTOR')
        carnet = identity(user)
        if actor.get('custom:carnet') and actor['custom:carnet'] != carnet:
            raise ValueError('INVALID_ACTOR')
        stored = d.get(db, 'IDEMPOTENCIA', {'idempotency_id': identifier})
        if stored:
            if stored['cuerpo_hash'] != digest:
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return stored['respuesta']
        before = user.get('saldo_centavos')
        if type(before) is not int or before < 0 or before > 99_999_999_999 - monto_centavos:
            raise ValueError('INVALID_WALLET')
        when = now()
        result = {'recarga_id': recarga_id, 'monto': format_centavos(monto_centavos),
            'saldo_despues_recarga': format_centavos(before + monto_centavos), 'moneda': 'GTQ'}
        recarga = {'idempotency_id': identifier, 'placa': placa, 'operacion': 'RECARGA',
            'cuerpo_hash': digest, 'monto_centavos': monto_centavos, 'respuesta': result,
            'creado_en': timestamp(when)}
        local_date = when.astimezone(ZoneInfo('America/Guatemala')).date().isoformat()
        movement = {'movimiento_id': recarga_id, 'tipo': 'RECARGA', 'placa': placa,
            'monto_centavos': monto_centavos, 'saldo_anterior_centavos': before,
            'saldo_centavos': before + monto_centavos, 'fecha': timestamp(when),
            'fecha_local': local_date, 'orden': timestamp(when) + '#' + recarga_id}
        event = make_event('saldo.recargado', recarga_id, 1,
            {'recarga_id': recarga_id, 'placa': placa, 'monto_centavos': monto_centavos,
             'saldo_centavos': before + monto_centavos},
            actor.get('request_id') or 'recarga:' + recarga_id, timestamp(when), carnet)
        update = {'Update': {'TableName': d.table('USUARIOS'),
            'Key': d.encode({'placa': placa}),
            'ConditionExpression': '#v = :v AND #s = :s AND #c = :c AND #b = :b',
            'UpdateExpression': 'SET #v = :next, #b = #b + :m',
            'ExpressionAttributeNames': {'#v': 'version', '#s': 'sub', '#c': 'carnet', '#b': 'saldo_centavos'},
            'ExpressionAttributeValues': d.encode({':v': user['version'], ':next': user['version']+1,
                ':s': user['sub'], ':c': carnet, ':b': before, ':m': monto_centavos})}}
        try:
            transact(db, [update, d.put('MOVIMIENTOS', movement),
                d.put('IDEMPOTENCIA', recarga), d.put('OUTBOX', outbox_item(event))])
            return result
        except Exception as exc:
            if not d.retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                # An ambiguous commit can have succeeded; the next request with
                # the same key reads the durable response before attempting credit.
                raise
            d.backoff(attempt)
    raise RuntimeError('TOPUP_RETRY_EXHAUSTED')


def calcular_tarifa(base_centavos: int, modalidad: str) -> int:
    if type(base_centavos) is not int or base_centavos <= 0:
        raise ValueError('INVALID_BASE_CENTAVOS')
    factors = {'TAG': Decimal('0.90'), 'APP': Decimal('1'), 'NO_REGISTRADO': Decimal('1.50')}
    if modalidad not in factors:
        raise ValueError('INVALID_MODALIDAD')
    return int((Decimal(base_centavos) * factors[modalidad]).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def transact(db, items):
    # Conditional stable business IDs survive beyond DynamoDB's token window.
    return db.transact_write_items(TransactItems=items)


def identity(user):
    if not user or any(not isinstance(user.get(k), str) or not user[k].strip() for k in ('sub', 'carnet')):
        raise ValueError('USER_IDENTITY_INCOMPLETE')
    if type(user.get('version')) is not int or user['version'] < 0:
        raise ValueError('INVALID_USER_VERSION')
    return user['carnet']


def classify(db, paso):
    if paso.get('clasificado_en'):
        if paso.get('modalidad') not in ('TAG', 'APP', 'NO_REGISTRADO') or type(paso.get('monto_centavos')) is not int:
            raise ValueError('INVALID_CLASSIFICATION_SNAPSHOT')
        return paso
    if paso['estado'] != 'REGISTRADO':
        raise ValueError('MISSING_CLASSIFICATION_SNAPSHOT')
    user = d.get(db, 'USUARIOS', {'placa': paso['placa']})
    toll = d.get(db, 'PEAJES', {'peaje_id': paso['peaje_id']})
    if not toll:
        raise ValueError('TOLL_NOT_FOUND')
    tag = d.get(db, 'TAGS', {'tag_id': paso['tag_id']}) if paso.get('tag_id') else None
    valid_tag = tag is not None and tag.get('activo') is True and tag.get('placa') == paso['placa']
    mode = ('TAG' if valid_tag else 'APP') if user else 'NO_REGISTRADO'
    carnet = identity(user) if user else os.environ['OWNER_CARNET']
    classified = dict(paso, modalidad=mode,
        monto_centavos=calcular_tarifa(toll['tarifa_base_centavos'], mode),
        tarifa_base_centavos=toll['tarifa_base_centavos'],
        clasificado_en=timestamp(now()), version=paso['version'] + 1,
        propietario_sub=user['sub'] if user else None, carnet=carnet)
    items = [d.put('PASOS', classified, paso, ['version', 'estado', 'clasificado_en']),
        d.check('USUARIOS', {'placa': paso['placa']}, user,
                ['placa', 'version', 'sub', 'carnet'] if user else ['placa']),
        d.check('PEAJES', {'peaje_id': paso['peaje_id']}, toll, ['tarifa_base_centavos'])]
    if paso.get('tag_id'):
        items.append(d.check('TAGS', {'tag_id': paso['tag_id']}, tag,
                             ['tag_id', 'placa', 'activo', 'version'] if tag else ['tag_id']))
    transact(db, items)
    return classified


def event_for(paso, kind, payload, when):
    return outbox_item(make_event(kind, paso['paso_id'], paso['version'], payload,
        'cobro:' + paso['paso_id'], timestamp(when), paso['carnet']))


def payload_for(paso):
    return {k: paso[k] for k in ('paso_id', 'placa', 'peaje_id', 'modalidad', 'monto_centavos')}


def pending_key(paso):
    return {'placa': paso['placa'], 'orden': paso['recibido_en'] + '#' + paso['paso_id']}


def first_pending(db, placa):
    page = db.query(TableName=d.table('PENDIENTES'), ConsistentRead=True,
        KeyConditionExpression='#p = :p', ExpressionAttributeNames={'#p': 'placa'},
        ExpressionAttributeValues=d.encode({':p': placa}), ScanIndexForward=True, Limit=1)
    return d.decode(page['Items'][0]) if page.get('Items') else None


def settle_pending(db, head):
    """Settle the current head only. Caller must requery after any conflict."""
    placa = head['placa']
    paso = d.get(db, 'PASOS', {'paso_id': head['paso_id']})
    if paso is None:
        raise ValueError('PENDING_PASS_MISSING')
    if paso['estado'] != 'PENDIENTE':
        return 'RELEER'
    if paso['placa'] != placa or pending_key(paso) != {'placa': placa, 'orden': head['orden']}:
        raise ValueError('PENDING_PASS_MISMATCH')
    amount = paso['monto_centavos']
    if type(amount) is not int or amount <= 0 or amount != head['monto_centavos']:
        raise ValueError('PENDING_AMOUNT_MISMATCH')
    current = now()
    due = datetime.fromisoformat(paso['vence_en'].replace('Z', '+00:00'))
    if due.tzinfo is None or paso['vence_en'] != head['vence_en']:
        raise ValueError('PENDING_DEADLINE_MISMATCH')
    if current >= due:
        return 'VENCIDO'
    user = d.get(db, 'USUARIOS', {'placa': placa})
    identity(user)
    if user['sub'] != paso['propietario_sub']:
        raise ValueError('PASS_OWNER_CHANGED')
    balance = user.get('saldo_centavos')
    if type(balance) is not int or balance < 0:
        raise ValueError('INVALID_WALLET')
    if balance < amount:
        return 'SIN_SALDO'
    new = dict(paso, estado='COBRADO', version=paso['version'] + 1,
               cobro_id=stable_id('cobro', paso['paso_id']), cobrado_en=timestamp(current))
    date_local = current.astimezone(ZoneInfo('America/Guatemala')).date().isoformat()
    movement = {'movimiento_id': new['cobro_id'], 'cobro_id': new['cobro_id'],
        'tipo': 'COBRO', 'placa': placa, 'paso_id': paso['paso_id'],
        'peaje_id': paso['peaje_id'], 'monto_centavos': amount,
        'saldo_anterior_centavos': balance, 'saldo_centavos': balance - amount,
        'modalidad': paso['modalidad'], 'fecha': timestamp(current), 'fecha_local': date_local,
        'peaje_fecha': paso['peaje_id'] + '#' + date_local,
        'orden': timestamp(current) + '#' + new['cobro_id']}
    update = {'Update': {'TableName': d.table('USUARIOS'),
        'Key': d.encode({'placa': placa}),
        'ConditionExpression': '#v = :v AND #s = :s AND #b = :b AND #b >= :m',
        'UpdateExpression': 'SET #v = :next, #b = #b - :m',
        'ExpressionAttributeNames': {'#v': 'version', '#s': 'sub', '#b': 'saldo_centavos'},
        'ExpressionAttributeValues': d.encode({':v': user['version'], ':next': user['version'] + 1,
            ':s': user['sub'], ':b': balance, ':m': amount})}}
    payload = dict(payload_for(new), cobro_id=new['cobro_id'], cobrado_en=new['cobrado_en'],
                   saldo_centavos=balance - amount)
    transact(db, [update,
        d.put('PASOS', new, paso, ['estado', 'version', 'monto_centavos', 'vence_en']),
        {'Delete': {'TableName': d.table('PENDIENTES'), 'Key': d.encode({'placa': placa, 'orden': head['orden']}),
            **d.guard(head, ['paso_id', 'monto_centavos', 'vence_en'])}},
        d.put('MOVIMIENTOS', movement),
        d.put('OUTBOX', event_for(new, 'paso.cobrado', payload, current))])
    return 'COBRADO'


def transition(db, paso):
    when = now()
    if paso['modalidad'] == 'NO_REGISTRADO':
        final = dict(paso, estado='NO_REGISTRADO', version=paso['version'] + 1)
        transact(db, [d.put('PASOS', final, paso, ['version', 'estado']),
                      d.put('OUTBOX', event_for(final, 'paso.no_registrado', payload_for(final), when))])
        return 'NO_REGISTRADO'

    user = d.get(db, 'USUARIOS', {'placa': paso['placa']})
    identity(user)
    if user['sub'] != paso['propietario_sub']:
        raise ValueError('PASS_OWNER_CHANGED')
    amount, balance = paso['monto_centavos'], user.get('saldo_centavos')
    if type(balance) is not int or balance < 0 or amount <= 0:
        raise ValueError('INVALID_WALLET')
    older = first_pending(db, paso['placa'])

    final = dict(paso, version=paso['version'] + 1)
    values = {':version': user['version'], ':next': user['version'] + 1,
              ':sub': user['sub'], ':amount': amount}
    update = {'TableName': d.table('USUARIOS'), 'Key': d.encode({'placa': paso['placa']}),
        'ExpressionAttributeNames': {'#v': 'version', '#s': 'sub', '#b': 'saldo_centavos'},
        'ConditionExpression': '#v = :version AND #s = :sub',
        'UpdateExpression': 'SET #v = :next'}
    items = []
    if balance >= amount and older is None:
        result = 'COBRADO'
        identifier = stable_id('cobro', paso['paso_id'])
        final.update(estado=result, cobro_id=identifier, cobrado_en=timestamp(when))
        update['ConditionExpression'] += ' AND #b >= :amount'
        update['UpdateExpression'] += ', #b = #b - :amount'
        local_date = when.astimezone(ZoneInfo('America/Guatemala')).date().isoformat()
        movement = {'movimiento_id': identifier, 'cobro_id': identifier,
            'tipo': 'COBRO', 'placa': paso['placa'], 'paso_id': paso['paso_id'],
            'peaje_id': paso['peaje_id'], 'monto_centavos': amount,
            'saldo_anterior_centavos': balance, 'saldo_centavos': balance - amount,
            'modalidad': paso['modalidad'], 'fecha': timestamp(when), 'fecha_local': local_date,
            'peaje_fecha': paso['peaje_id'] + '#' + local_date,
            'orden': timestamp(when) + '#' + identifier}
        items.append(d.put('MOVIMIENTOS', movement))
        payload = dict(payload_for(final), cobro_id=identifier,
                       cobrado_en=timestamp(when), saldo_centavos=balance - amount)
        kind = 'paso.cobrado'
    else:
        # This branch is chosen only by a consistent balance read and confirmed
        # by the transaction. Transaction errors never mean insufficient funds.
        import boto3
        result = 'PENDIENTE'
        due = deadline(boto3.client('ssm'), os.environ['RECARGA_TIMEOUT_PARAMETER'], when)
        final.update(estado=result, vence_en=due)
        if older is None:
            update['ConditionExpression'] += ' AND #b < :amount'
        else:
            items.append(d.check('PENDIENTES',
                {'placa': older['placa'], 'orden': older['orden']}, older,
                ['paso_id', 'monto_centavos', 'vence_en']))
        pending = dict(pending_key(paso), paso_id=paso['paso_id'],
            recibido_en=paso['recibido_en'], monto_centavos=amount,
            modalidad=paso['modalidad'], vence_en=due, propietario_sub=user['sub'])
        items.append(d.put('PENDIENTES', pending))
        payload = dict(payload_for(final), vence_en=due, saldo_centavos=balance)
        kind = 'paso.pendiente'
    # Pin balance too: protects snapshots even from an out-of-band writer that
    # incorrectly changes balance without incrementing version.
    update['ConditionExpression'] += ' AND #b = :balance'
    values[':balance'] = balance
    update['ExpressionAttributeValues'] = d.encode(values)
    items.extend([{'Update': update}, d.put('PASOS', final, paso, ['version', 'estado']),
                  d.put('OUTBOX', event_for(final, kind, payload, when))])
    transact(db, items)
    return result


def cobrar_paso(paso_id: str) -> str:
    if not isinstance(paso_id, str) or not paso_id:
        raise ValueError('INVALID_PASO_ID')
    db = d.client()
    for attempt in range(MAX_ATTEMPTS):
        try:
            paso = d.get(db, 'PASOS', {'paso_id': paso_id})
            if not paso:
                raise ValueError('PASS_NOT_FOUND')
            if paso['estado'] in TERMINAL or paso['estado'] == 'PENDIENTE':
                return 'SIN_CAMBIO'
            if paso['estado'] != 'REGISTRADO':
                raise ValueError('INVALID_PASS_STATE')
            return transition(db, classify(db, paso))
        except Exception as exc:
            if not d.retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            d.backoff(attempt)
    raise RuntimeError('RETRIES_EXHAUSTED')
