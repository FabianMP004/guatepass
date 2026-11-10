import os
import time
import boto3
from boto3.dynamodb.conditions import Key
from eventos.publicar import publish_ids

def recuperar_outbox() -> dict:
    table = boto3.resource('dynamodb').Table(os.environ['OUTBOX_TABLE'])
    deadline = time.monotonic() + 40
    stats = {'revisados': 0, 'fallidos': 0, 'incompleto': False}
    for shard in range(4):
        cursor = None
        while True:
            if time.monotonic() >= deadline:
                stats['incompleto'] = True
                return stats
            args = {'IndexName': 'PorPendiente', 'Limit': 100,
                    'KeyConditionExpression': Key('pendiente_shard').eq(f'PENDING#{shard:02d}')}
            if cursor:
                args['ExclusiveStartKey'] = cursor
            page = table.query(**args)
            ids = [r['evento_id'] for r in page.get('Items', [])]
            stats['revisados'] += len(ids)
            stats['fallidos'] += len(publish_ids(ids))
            cursor = page.get('LastEvaluatedKey')
            if not cursor:
                break
    return stats

def handler(event, context):
    result = recuperar_outbox()
    if result['fallidos'] or result['incompleto']:
        raise RuntimeError(f'OUTBOX_RECOVERY_INCOMPLETE: {result}')
    return result
