"""Dev-only alarm drill. Restores the billing consumer and deletes only its own DLQ marker."""
import argparse
import json
import time
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError


def wait_alarm(cw, name, desired, limit=300):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        alarms = cw.describe_alarms(AlarmNames=[name])['MetricAlarms']
        if not alarms:
            raise RuntimeError('ALARM_NOT_FOUND: ' + name)
        state = alarms[0]['StateValue']
        if state == desired:
            print(json.dumps({'alarm': name, 'state': state}), flush=True)
            return
        time.sleep(10)
    raise RuntimeError('ALARM_DID_NOT_REACH_' + desired + ': ' + name)


def queue_empty(sqs, url):
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=[
        'ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible'])['Attributes']
    return all(int(attrs.get(key, '0')) == 0 for key in
               ('ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--phase', choices=['backlog', 'dlq'], required=True)
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    stack = session.client('cloudformation').describe_stacks(StackName=args.stack)['Stacks'][0]
    if next((p['ParameterValue'] for p in stack['Parameters']
             if p['ParameterKey'] == 'Environment'), None) != 'dev':
        raise SystemExit('El ensayo solo está habilitado para dev.')
    o = {row['OutputKey']: row['OutputValue'] for row in stack['Outputs']}
    sqs, cw = session.client('sqs'), session.client('cloudwatch')
    if args.phase == 'dlq':
        url, name = o['PasosDLQUrl'], o['PasosDLQAlarmName']
        if not queue_empty(sqs, url):
            raise SystemExit('La DLQ contiene mensajes previos; revisar antes del ensayo.')
        marker = 'guatepass-drill-' + uuid4().hex
        sqs.send_message(QueueUrl=url, MessageBody=marker)
        try:
            wait_alarm(cw, name, 'ALARM')
        finally:
            deadline = time.monotonic() + 100
            removed = False
            while time.monotonic() < deadline and not removed:
                page = sqs.receive_message(QueueUrl=url, WaitTimeSeconds=2, MaxNumberOfMessages=10)
                for message in page.get('Messages', []):
                    if message['Body'] == marker:
                        sqs.delete_message(QueueUrl=url, ReceiptHandle=message['ReceiptHandle'])
                        removed = True
            if not removed:
                raise RuntimeError('DRILL_MARKER_NOT_REMOVED')
        wait_alarm(cw, name, 'OK')
        return

    url, name = o['PasosQueueUrl'], o['PasosBacklogAlarmName']
    if not queue_empty(sqs, url):
        raise SystemExit('La cola de pasos no está vacía; revisar antes del ensayo.')
    lam = session.client('lambda')
    mappings = lam.list_event_source_mappings(EventSourceArn=o['PasosQueueArn'],
        FunctionName=o['CobrarFunctionName'])['EventSourceMappings']
    if len(mappings) != 1 or mappings[0]['State'] != 'Enabled':
        raise SystemExit('No se encontró exactamente un consumidor habilitado.')
    uuid = mappings[0]['UUID']
    # Reuse one already accepted, valid event. Billing is idempotent by paso_id.
    outbox = session.resource('dynamodb').Table(o['OutboxTableName'])
    source = None
    arguments = {'ProjectionExpression': '#kind, #detail, #source',
                 'ExpressionAttributeNames': {'#kind': 'detail-type', '#source': 'source', '#detail': 'detail'},
                 'FilterExpression': '#kind = :kind',
                 'ExpressionAttributeValues': {':kind': 'paso.registrado'}}
    while True:
        page = outbox.scan(**arguments)
        if page.get('Items'):
            row = page['Items'][0]
            source = {'source': row['source'], 'detail-type': row['detail-type'],
                      'detail': json.loads(row['detail'])}
            break
        if not page.get('LastEvaluatedKey'):
            break
        arguments['ExclusiveStartKey'] = page['LastEvaluatedKey']
    if not source:
        raise SystemExit('Se necesita un paso previo para ensayar el backlog.')
    lam.update_event_source_mapping(UUID=uuid, Enabled=False)
    try:
        for _ in range(30):
            if lam.get_event_source_mapping(UUID=uuid)['State'] == 'Disabled':
                break
            time.sleep(2)
        else:
            raise RuntimeError('BILLING_CONSUMER_NOT_DISABLED')
        # State=Disabled can precede the last in-flight poller finishing.
        time.sleep(30)
        for _ in range(12):
            sqs.send_message(QueueUrl=url, MessageBody=json.dumps(source))
        for _ in range(12):
            attributes = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=[
                'ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible'])['Attributes']
            visible = int(attributes.get('ApproximateNumberOfMessages', '0'))
            in_flight = int(attributes.get('ApproximateNumberOfMessagesNotVisible', '0'))
            if visible >= 10:
                print(json.dumps({'visible_messages': visible, 'in_flight': in_flight,
                                  'consumer': 'Disabled'}), flush=True)
                break
            time.sleep(10)
        else:
            raise RuntimeError(f'BACKLOG_NOT_VISIBLE: visible={visible} in_flight={in_flight}')
        wait_alarm(cw, name, 'ALARM')
    finally:
        for attempt in range(20):
            try:
                lam.update_event_source_mapping(UUID=uuid, Enabled=True)
                break
            except ClientError as exc:
                if exc.response['Error']['Code'] != 'ResourceConflictException' or attempt == 19:
                    raise
                time.sleep(3)
        for _ in range(45):
            if lam.get_event_source_mapping(UUID=uuid)['State'] == 'Enabled':
                break
            time.sleep(2)
        else:
            raise RuntimeError('BILLING_CONSUMER_NOT_RESTORED')
    wait_alarm(cw, name, 'OK')
    print(json.dumps({'consumer': 'restored', 'replayed_messages': 12}), flush=True)


if __name__ == '__main__':
    main()
