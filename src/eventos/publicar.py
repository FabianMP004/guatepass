"""At-least-once publication; stable detail.evento_id survives retries."""
import json
import logging
import os
from datetime import datetime, timezone
import boto3
from botocore.config import Config
from common.models import validate_event
from common.events import from_outbox
from common.observability import emit

def publicar(eventos: list[dict]) -> list[str]:
    for event in eventos:
        validate_event(event)
    if not eventos:
        return []
    bus = boto3.client('events', config=Config(connect_timeout=2, read_timeout=3, retries={'total_max_attempts':2}))
    # A nonexistent bus can silently accept PutEvents. Verify before publishing.
    try:
        bus.describe_event_bus(Name=os.environ['EVENT_BUS_NAME'])
    except Exception:
        logging.exception('Event bus unavailable')
        return [e['detail']['evento_id'] for e in eventos]
    failed = []
    for offset in range(0, len(eventos), 10):
        batch = eventos[offset:offset+10]
        ids = [e['detail']['evento_id'] for e in batch]
        try:
            result = bus.put_events(Entries=[{
                'EventBusName': os.environ['EVENT_BUS_NAME'], 'Source': e['source'],
                'DetailType': e['detail-type'], 'Detail': json.dumps(e['detail'], ensure_ascii=False),
            } for e in batch])
            entries = result.get('Entries', [])
            errors = sum(bool(e.get('ErrorCode')) for e in entries)
            if len(entries) != len(batch) or result.get('FailedEntryCount') != errors:
                failed.extend(ids)
                continue
            failed.extend(identifier for identifier, entry in zip(ids, entries)
                          if entry.get('ErrorCode') or not entry.get('EventId'))
        except Exception:
            logging.exception('PutEvents failed')
            failed.extend(ids)
    return failed

def publish_ids(ids):
    table = boto3.resource('dynamodb').Table(os.environ['OUTBOX_TABLE'])
    failed, pending = [], []
    for identifier in dict.fromkeys(ids):
        try:
            row = table.get_item(Key={'evento_id': identifier}, ConsistentRead=True).get('Item')
            if row is None:
                raise ValueError('OUTBOX_ITEM_MISSING')
            if not row.get('enviado'):
                pending.append(from_outbox(row))
        except Exception:
            logging.exception('Outbox read failed id=%s', identifier)
            failed.append(identifier)
    try:
        rejected = set(publicar(pending))
    except Exception:
        logging.exception('Outbox publication unavailable')
        rejected = {e['detail']['evento_id'] for e in pending}
    for event in pending:
        identifier = event['detail']['evento_id']
        if identifier in rejected:
            emit(paso_id=event['detail'].get('paso_id'), evento_id=identifier,
                 request_id=event['detail'].get('request_id'), error='PUBLICATION_FAILED')
            failed.append(identifier)
            continue
        try:
            table.update_item(Key={'evento_id': identifier},
                UpdateExpression='SET enviado=:yes, enviado_en=:now REMOVE pendiente_shard',
                ConditionExpression='attribute_exists(evento_id)',
                ExpressionAttributeValues={':yes': True, ':now': datetime.now(timezone.utc).isoformat()})
            emit(paso_id=event['detail'].get('paso_id'), evento_id=identifier,
                 request_id=event['detail'].get('request_id'), estado='PUBLICADO')
        except Exception:
            emit(paso_id=event['detail'].get('paso_id'), evento_id=identifier,
                 request_id=event['detail'].get('request_id'), error='OUTBOX_MARK_FAILED')
            logging.exception('Accepted event not marked id=%s', identifier)
            failed.append(identifier)
    return failed

def handler(event, context):
    if 'Records' not in event:
        from eventos.recuperar_outbox import handler as recover
        return recover(event, context)
    records = [r for r in event['Records'] if r.get('eventName') == 'INSERT']
    ids, failures = {}, []
    for row in records:
        sequence = row['dynamodb']['SequenceNumber']
        try:
            identifier = row['dynamodb']['Keys']['evento_id']['S']
            ids.setdefault(identifier, []).append(sequence)
        except (KeyError, TypeError):
            failures.append({'itemIdentifier': sequence})
    rejected = set(publish_ids(list(ids)))
    failures.extend({'itemIdentifier': seq} for identifier in rejected for seq in ids[identifier])
    return {'batchItemFailures': failures}
