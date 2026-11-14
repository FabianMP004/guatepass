"""Durable dispute decisions, timeout resolution and wait callbacks."""
import json
import os
from datetime import datetime, timezone
import boto3
from common import dynamo as d
from disputas.devolucion import _load, aplicar_devolucion
from common.observability import emit


def _now():
    return datetime.now(timezone.utc)


def _due(row):
    return datetime.fromisoformat(row['vence_en'].replace('Z', '+00:00'))


def _wait_key(disputa_id):
    return {'espera_id': 'DISPUTA#' + disputa_id}


def _decide(disputa_id, decision, reason, actor, automatic=False):
    db = d.client()
    for attempt in range(6):
        row = _load(db, disputa_id)
        if row['estado'] != 'ABIERTA':
            if automatic:
                return {'disputa_id': disputa_id, 'estado': row['estado']}
            raise ValueError('DISPUTE_ALREADY_RESOLVED')
        if row.get('decision'):
            if automatic or (row['decision'] == decision and row.get('resuelta_por') == actor['sub']):
                result = aplicar_devolucion(disputa_id)
                completar(disputa_id)
                emit(paso_id=row.get('paso_id'), request_id='disputa:' + disputa_id,
                     estado=result['estado'])
                return result
            raise ValueError('DISPUTE_ALREADY_RESOLVED')
        if not automatic and _now() >= _due(row):
            raise ValueError('DISPUTE_EXPIRED')
        updated = dict(row, decision=decision, motivo_decision=reason,
                       resuelta_por=actor['sub'], carnet_decisor=actor['carnet'],
                       resolucion='AUTOMATICA' if automatic else 'MANUAL',
                       decision_en=_now().isoformat(timespec='microseconds').replace('+00:00', 'Z'),
                       version=row['version'] + 1)
        try:
            db.put_item(TableName=d.table('DISPUTAS'), Item=d.encode(updated),
                        **d.guard(row, ['version', 'estado', 'decision']))
        except Exception as exc:
            if not d.retryable(exc) and getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
                raise
            if attempt == 5:
                raise
            d.backoff(attempt)
            continue
        result = aplicar_devolucion(disputa_id)
        completar(disputa_id)
        emit(paso_id=row.get('paso_id'), request_id='disputa:' + disputa_id,
             estado=result['estado'])
        return result
    raise RuntimeError('DECISION_RETRY_EXHAUSTED')


def decidir_disputa(disputa_id: str, resultado: str, motivo: str, actor: dict) -> dict:
    if resultado not in ('APROBAR', 'RECHAZAR'):
        raise ValueError('INVALID_DECISION')
    if not isinstance(motivo, str) or not 1 <= len(motivo.strip()) <= 1000:
        raise ValueError('INVALID_REASON')
    if not actor.get('sub') or not actor.get('carnet') or actor.get('role') != 'atencion':
        raise PermissionError('FORBIDDEN')
    return _decide(disputa_id, resultado, motivo.strip(), actor)


def vencer_disputa(disputa_id):
    row = _load(d.client(), disputa_id)
    if row['estado'] != 'ABIERTA':
        return row['estado']
    if row.get('decision'):
        result = aplicar_devolucion(disputa_id)
        completar(disputa_id)
        return result['estado']
    if _now() < _due(row):
        return 'NO_VENCIDA'
    actor = {'role': 'SISTEMA', 'sub': 'SISTEMA', 'carnet': os.environ['OWNER_CARNET']}
    return _decide(disputa_id, 'APROBAR', 'Vencimiento automático', actor, True)['estado']


def preparar(disputa_id):
    row = _load(d.client(), disputa_id)
    if row['estado'] != 'ABIERTA' or row.get('decision'):
        return {'status': 'TERMINADA', 'seconds': 1}
    import math
    remaining = (_due(row) - _now()).total_seconds()
    return {'status': 'VENCIDA' if remaining <= 0 else 'ESPERAR',
            'seconds': max(1, math.ceil(remaining))}


def iniciar(disputa_id):
    state = preparar(disputa_id)
    if state['status'] == 'TERMINADA':
        return 'SIN_CAMBIO'
    if state['status'] == 'VENCIDA':
        return vencer_disputa(disputa_id)
    sfn = boto3.client('stepfunctions')
    try:
        return sfn.start_execution(stateMachineArn=os.environ['DISPUTA_STATE_MACHINE_ARN'],
            name=disputa_id, input=json.dumps({'disputa_id': disputa_id}))['executionArn']
    except sfn.exceptions.ExecutionAlreadyExists:
        arn = os.environ['DISPUTA_STATE_MACHINE_ARN'].replace(':stateMachine:', ':execution:') + ':' + disputa_id
        if sfn.describe_execution(executionArn=arn)['status'] == 'FAILED':
            raise RuntimeError('DISPUTE_WAIT_REDRIVE_REQUIRED')
        return arn


def registrar_token(event, context):
    did, token, execution = event['disputa_id'], event['token'], event['execution_arn']
    if not all(isinstance(x, str) and x for x in (did, token, execution)):
        raise ValueError('INVALID_WAIT_TOKEN')
    db = d.client()
    old = d.get(db, 'ESPERAS', _wait_key(did))
    if old and old.get('execution_arn') not in (None, execution):
        raise ValueError('WAIT_EXECUTION_MISMATCH')
    row = dict(old or {}, **_wait_key(did), tipo='DISPUTA', disputa_id=did,
               token=token, execution_arn=execution)
    try:
        db.put_item(TableName=d.table('ESPERAS'), Item=d.encode(row),
                    **d.guard(old, ['token', 'execution_arn'] if old else ['espera_id']))
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        latest = d.get(db, 'ESPERAS', _wait_key(did))
        if not latest or latest.get('token') != token or latest.get('execution_arn') != execution:
            raise
    # Completion may have won before the token was saved.
    if _load(db, did).get('estado') in ('APROBADA', 'RECHAZADA'):
        completar(did)
    return {'registrado': True}


def completar(disputa_id):
    db = d.client()
    row = _load(db, disputa_id)
    if row['estado'] not in ('APROBADA', 'RECHAZADA'):
        return 'PENDIENTE'
    wait = d.get(db, 'ESPERAS', _wait_key(disputa_id))
    if not wait:
        try:
            db.put_item(TableName=d.table('ESPERAS'),
                Item=d.encode(dict(_wait_key(disputa_id), tipo='DISPUTA', disputa_id=disputa_id, cerrar=True)),
                **d.guard(None, ['espera_id']))
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
                raise
        wait = d.get(db, 'ESPERAS', _wait_key(disputa_id))
    if not wait or not wait.get('token'):
        return 'SENAL_GUARDADA'
    sfn = boto3.client('stepfunctions')
    try:
        sfn.send_task_success(taskToken=wait['token'], output=json.dumps({'estado': row['estado']}))
    # After waitForTaskToken times out, Step Functions has already invalidated
    # the token. The timeout branch must still complete after the durable refund.
    except (sfn.exceptions.TaskDoesNotExist, sfn.exceptions.InvalidToken,
            sfn.exceptions.TaskTimedOut):
        if _load(db, disputa_id)['estado'] not in ('APROBADA', 'RECHAZADA'):
            raise
    try:
        db.delete_item(TableName=d.table('ESPERAS'), Key=d.encode(_wait_key(disputa_id)),
                       **d.guard(wait, ['token']))
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
    return 'COMPLETADA'


def check_handler(event, context):
    return preparar(event['disputa_id'])


def timeout_handler(event, context):
    return vencer_disputa(event['disputa_id'])


def reconcile_handler(event, context):
    db = d.client()
    cursor = None
    count = 0
    while True:
        args = {'TableName': d.table('DISPUTAS'), 'ConsistentRead': True,
                'FilterExpression': 'attribute_exists(#id) AND #state = :open',
                'ExpressionAttributeNames': {'#id': 'disputa_id', '#state': 'estado'},
                'ExpressionAttributeValues': d.encode({':open': 'ABIERTA'})}
        if cursor:
            args['ExclusiveStartKey'] = cursor
        page = db.scan(**args)
        for raw in page.get('Items', []):
            if context and context.get_remaining_time_in_millis() < 8000:
                raise RuntimeError('RECONCILE_TIME_BUDGET')
            row = d.decode(raw)
            if row.get('decision') or _now() >= _due(row):
                vencer_disputa(row['disputa_id'])
            else:
                iniciar(row['disputa_id'])
            count += 1
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            break
    cursor = None
    closed = 0
    while True:
        args = {'TableName': d.table('ESPERAS'), 'ConsistentRead': True,
                'FilterExpression': 'begins_with(#key, :prefix)',
                'ExpressionAttributeNames': {'#key': 'espera_id'},
                'ExpressionAttributeValues': d.encode({':prefix': 'DISPUTA#'})}
        if cursor:
            args['ExclusiveStartKey'] = cursor
        page = db.scan(**args)
        for raw in page.get('Items', []):
            if context and context.get_remaining_time_in_millis() < 8000:
                raise RuntimeError('RECONCILE_TIME_BUDGET')
            row = d.decode(raw)
            if _load(db, row['disputa_id'])['estado'] in ('APROBADA', 'RECHAZADA'):
                if completar(row['disputa_id']) == 'COMPLETADA':
                    closed += 1
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            break
    return {'revisadas': count, 'cierres': closed}
