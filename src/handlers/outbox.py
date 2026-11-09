"""At-least-once publisher. Consumers MUST deduplicate stable evento_id.
A crash after PutEvents but before UpdateItem can publish a duplicate.
Scheduled recovery reads sparse PENDING#00..03 GSI shards; no money decisions.
"""
import json, os, logging
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

def publish(table, bus, event_id):
    item=table.get_item(Key={'evento_id':event_id},ConsistentRead=True).get('Item')
    if not item or item.get('enviado'): return
    detail=item['detail']
    # Contract: detail persisted as a JSON string, avoiding Decimal serialization.
    d=json.loads(detail)
    if d.get('grupo')!='guatepass' or not d.get('carnet') or d.get('evento_id')!=event_id:
        raise ValueError('INVALID_EVENT_AUDIT')
    result=bus.put_events(Entries=[{'EventBusName':os.environ['EVENT_BUS_NAME'],'Source':item['source'],'DetailType':item['detail-type'],'Detail':detail}])
    if result.get('FailedEntryCount'): raise RuntimeError('PUT_EVENTS_FAILED')
    table.update_item(Key={'evento_id':event_id},UpdateExpression='SET enviado=:yes, enviado_en=:now REMOVE pendiente_shard',ExpressionAttributeValues={':yes':True,':now':datetime.now(timezone.utc).isoformat()})

def handler(event,context):
    table=boto3.resource('dynamodb').Table(os.environ['OUTBOX_TABLE']);bus=boto3.client('events')
    if 'Records' in event:
        failed=[];decoder=TypeDeserializer()
        for record in event['Records']:
            if record.get('eventName')!='INSERT': continue
            try:
                image=record['dynamodb']['NewImage']
                publish(table,bus,decoder.deserialize(image['evento_id']))
            except Exception:
                logging.exception('outbox publish failed')
                failed.append({'itemIdentifier':record['dynamodb']['SequenceNumber']})
        return {'batchItemFailures':failed}
    failed=0
    for shard in range(4):
        rows=table.query(IndexName='PorPendiente',KeyConditionExpression=Key('pendiente_shard').eq(f'PENDING#{shard:02d}'),Limit=100).get('Items',[])
        for row in rows:
            try:publish(table,bus,row['evento_id'])
            except Exception:
                failed+=1;logging.exception('outbox recovery failed evento_id=%s',row['evento_id'])
    if failed:raise RuntimeError('OUTBOX_RECOVERY_FAILED')
    return {'status':'ok'}
