"""Publish pending Outbox count and oldest age without exposing event payloads."""
from datetime import datetime, timezone
import os

import boto3


def handler(event, context):
    table = boto3.resource('dynamodb').Table(os.environ['OUTBOX_TABLE'])
    now = datetime.now(timezone.utc)
    pending, oldest = 0, 0.0
    for shard in range(4):
        arguments = {'IndexName': 'PorPendiente',
            'KeyConditionExpression': '#p = :p',
            'ExpressionAttributeNames': {'#p': 'pendiente_shard'},
            'ExpressionAttributeValues': {':p': f'PENDING#{shard:02d}'},
            'ProjectionExpression': 'creado_en', 'Limit': 100}
        while True:
            page = table.query(**arguments)
            pending += len(page.get('Items', []))
            for row in page.get('Items', []):
                instant = datetime.fromisoformat(row['creado_en'].replace('Z', '+00:00'))
                oldest = max(oldest, (now-instant).total_seconds())
            if not page.get('LastEvaluatedKey'):
                break
            arguments['ExclusiveStartKey'] = page['LastEvaluatedKey']
    boto3.client('cloudwatch').put_metric_data(Namespace='GuatePass/Observability',
        MetricData=[{'MetricName': name, 'Dimensions': [{'Name': 'Stack', 'Value': os.environ['STACK_NAME']}],
                     'Value': max(0, value), 'Unit': unit} for name, value, unit in (
                         ('OutboxPendingCount', pending, 'Count'),
                         ('OutboxOldestSeconds', oldest, 'Seconds'))])
    return {'pending': pending, 'oldest_seconds': round(max(0, oldest), 2)}
