"""Small DynamoDB helpers. Conditions always target base table keys."""
import os
import random
import time
from decimal import Decimal


def client():
    import boto3
    return boto3.client('dynamodb')


def table(name):
    return os.environ[name + '_TABLE']


def encode(item):
    from boto3.dynamodb.types import TypeSerializer
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in item.items()}


def decode(item):
    from boto3.dynamodb.types import TypeDeserializer
    deserializer = TypeDeserializer()
    def normalize(value):
        if isinstance(value, Decimal) and value == value.to_integral_value():
            return int(value)
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value
    return {k: normalize(deserializer.deserialize(v)) for k, v in item.items()}


def get(db, name, key):
    item = db.get_item(TableName=table(name), Key=encode(key), ConsistentRead=True).get('Item')
    return decode(item) if item else None


def guard(snapshot, fields):
    """Compare read snapshot, including absence/NULL; no reserved names exposed."""
    names, values, parts = {}, {}, []
    for i, field in enumerate(fields):
        alias = '#g' + str(i)
        names[alias] = field
        if snapshot is None or field not in snapshot:
            parts.append('attribute_not_exists(' + alias + ')')
        else:
            val = ':g' + str(i)
            values[val] = snapshot[field]
            parts.append(alias + ' = ' + val)
    result = {'ConditionExpression': ' AND '.join(parts), 'ExpressionAttributeNames': names}
    if values:
        result['ExpressionAttributeValues'] = encode(values)
    return result


def check(name, key, snapshot, fields):
    return {'ConditionCheck': {'TableName': table(name), 'Key': encode(key),
                              **guard(snapshot, fields)}}


def put(name, item, snapshot=None, fields=None):
    return {'Put': {'TableName': table(name), 'Item': encode(item),
                    **guard(snapshot, fields or [next(iter(item))])}}


def retryable(exc):
    error = getattr(exc, 'response', {})
    code = error.get('Error', {}).get('Code')
    if code == 'TransactionCanceledException':
        reasons = {r.get('Code') for r in error.get('CancellationReasons', [])}
        # Unknown reasons retry boundedly, never infer insufficient funds.
        return not reasons or reasons <= {
            None, 'None', 'ConditionalCheckFailed', 'TransactionConflict',
            'ProvisionedThroughputExceeded', 'ThrottlingError'}
    return code in {'TransactionConflictException', 'TransactionInProgressException',
                    'ProvisionedThroughputExceededException', 'ThrottlingException',
                    'RequestLimitExceeded', 'InternalServerError'}


def backoff(attempt):
    time.sleep(random.uniform(0, min(0.025 * 2 ** attempt, 0.3)))
