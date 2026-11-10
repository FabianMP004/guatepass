"""Strongly consistent FIFO drain on base table keys; continuation is durable."""
import json
import logging
import os
from common import dynamo as d
from common import money

MAX_PER_RUN = 20
MIN_MILLIS = 8000


def key(placa):
    return {'espera_id': 'DRENAJE#' + placa}


def send_continuation(row):
    import boto3
    boto3.client('sqs').send_message(QueueUrl=os.environ['PASOS_QUEUE_URL'],
        MessageBody=json.dumps({'internal': 'continuar_drenaje',
            'placa': row['placa'], 'generation': row['version']}))


def schedule(db, placa):
    for attempt in range(money.MAX_ATTEMPTS):
        old = d.get(db, 'ESPERAS', key(placa))
        row = {'espera_id': key(placa)['espera_id'], 'placa': placa,
            'version': (old['version'] + 1) if old else 1, 'tipo': 'DRENAJE'}
        try:
            db.put_item(TableName=d.table('ESPERAS'), Item=d.encode(row),
                **d.guard(old, ['version'] if old else ['espera_id']))
            break
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code != 'ConditionalCheckFailedException' and not d.retryable(exc):
                raise
            if attempt == money.MAX_ATTEMPTS - 1:
                raise
            d.backoff(attempt)
    # The row is durable before enqueueing. Recovery resends if SQS fails.
    send_continuation(row)


def clear(db, placa):
    row = d.get(db, 'ESPERAS', key(placa))
    if row:
        try:
            db.delete_item(TableName=d.table('ESPERAS'), Key=d.encode(key(placa)),
                **d.guard(row, ['version']))
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
                raise
            # A newer continuation owns the record.


def cobrar_pendientes(placa: str, *, context=None) -> list[str]:
    if not isinstance(placa, str) or not placa:
        raise ValueError('INVALID_PLACA')
    db = d.client()
    paid = []
    retries = 0
    while True:
        if len(paid) >= MAX_PER_RUN:
            # Exactly MAX_PER_RUN can also mean that the queue is now empty.
            # Clear any prior continuation before requesting another invocation.
            if money.first_pending(db, placa):
                schedule(db, placa)
            else:
                clear(db, placa)
            return paid
        if context and context.get_remaining_time_in_millis() < MIN_MILLIS:
            # Keep the wake-up durable even if the current Lambda runs out of time.
            schedule(db, placa)
            return paid
        head = money.first_pending(db, placa)
        if not head:
            clear(db, placa)
            return paid
        try:
            status = money.settle_pending(db, head)
        except Exception as exc:
            if not d.retryable(exc) or retries >= money.MAX_ATTEMPTS:
                raise
            retries += 1
            d.backoff(retries)
            continue
        if status == 'RELEER':
            retries += 1
            if retries >= money.MAX_ATTEMPTS:
                raise RuntimeError('PENDING_RETRY_LIMIT')
            continue
        retries = 0
        if status != 'COBRADO':
            # Do not jump to a later debt when the first one lacks money or
            # has already expired. Facturación handles expired debts later.
            clear(db, placa)
            return paid
        paid.append(head['paso_id'])


def recover(event, context):
    """Scheduled once per minute. Existing tokens in Esperas are untouched."""
    db = d.client()
    cursor = None
    result = {'revisados': 0, 'reenviados': 0}
    while True:
        kwargs = {'TableName': d.table('ESPERAS'), 'ConsistentRead': True,
            'FilterExpression': 'begins_with(#k, :prefix)',
            'ExpressionAttributeNames': {'#k': 'espera_id'},
            'ExpressionAttributeValues': d.encode({':prefix': 'DRENAJE#'})}
        if cursor:kwargs['ExclusiveStartKey'] = cursor
        page = db.scan(**kwargs)
        for raw in page.get('Items', []):
            row = d.decode(raw)
            result['revisados'] += 1
            if context and context.get_remaining_time_in_millis() < 8000:
                raise RuntimeError('CONTINUATION_SCAN_INCOMPLETE')
            send_continuation(row)
            result['reenviados'] += 1
        cursor = page.get('LastEvaluatedKey')
        if not cursor:break
    return result
