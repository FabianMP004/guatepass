"""Read recent CloudWatch evidence, including missing-data status for each metric."""
import argparse
from datetime import datetime, timedelta, timezone
import json

import boto3
from botocore.config import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--minutes', type=int, default=30)
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    config = Config(connect_timeout=3, read_timeout=8,
                    retries={'mode': 'standard', 'total_max_attempts': 2})
    outputs = {row['OutputKey']: row['OutputValue'] for row in session.client('cloudformation', config=config)
        .describe_stacks(StackName=args.stack)['Stacks'][0]['Outputs']}
    api_name = session.client('apigateway', config=config).get_rest_api(restApiId=outputs['RestApiId'])['name']
    metrics = [
        ('api_latency_ms', 'AWS/ApiGateway', 'Latency', {'ApiName': api_name}, 'p99'),
        ('api_errors_4xx', 'AWS/ApiGateway', '4XXError', {'ApiName': api_name}, 'Sum'),
        ('api_errors_5xx', 'AWS/ApiGateway', '5XXError', {'ApiName': api_name}, 'Sum'),
        ('webhook_errors', 'AWS/Lambda', 'Errors', {'FunctionName': args.stack+'-webhook'}, 'Sum'),
        ('webhook_throttles', 'AWS/Lambda', 'Throttles', {'FunctionName': args.stack+'-webhook'}, 'Sum'),
        ('billing_errors', 'AWS/Lambda', 'Errors', {'FunctionName': outputs['CobrarFunctionName']}, 'Sum'),
        ('billing_throttles', 'AWS/Lambda', 'Throttles', {'FunctionName': outputs['CobrarFunctionName']}, 'Sum'),
        ('publisher_errors', 'AWS/Lambda', 'Errors', {'FunctionName': args.stack+'-outboxpublisher'}, 'Sum'),
        ('publisher_throttles', 'AWS/Lambda', 'Throttles', {'FunctionName': args.stack+'-outboxpublisher'}, 'Sum'),
        ('queue_depth', 'AWS/SQS', 'ApproximateNumberOfMessagesVisible',
         {'QueueName': args.stack+'-cola-pasos'}, 'Maximum'),
        ('queue_age_seconds', 'AWS/SQS', 'ApproximateAgeOfOldestMessage',
         {'QueueName': args.stack+'-cola-pasos'}, 'Maximum'),
        ('dlq_depth', 'AWS/SQS', 'ApproximateNumberOfMessagesVisible',
         {'QueueName': args.stack+'-cola-pasos-dlq'}, 'Maximum'),
        ('outbox_pending', 'GuatePass/Observability', 'OutboxPendingCount',
         {'Stack': args.stack}, 'Maximum'),
        ('outbox_oldest_seconds', 'GuatePass/Observability', 'OutboxOldestSeconds',
         {'Stack': args.stack}, 'Maximum'),
        ('publication_failures', 'GuatePass/Observability', 'PublishFailures', {}, 'Sum'),
        ('dispute_wait_failed', 'AWS/States', 'ExecutionsFailed',
         {'StateMachineArn': outputs['EsperaDisputaArn']}, 'Sum'),
        ('topup_wait_failed', 'AWS/States', 'ExecutionsFailed',
         {'StateMachineArn': outputs['EsperaRecargaArn']}, 'Sum'),
    ]
    cw = session.client('cloudwatch', config=config)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=args.minutes)
    queries = [{'Id': f'm{index}', 'ReturnData': True,
        'MetricStat': {'Metric': {'Namespace': namespace, 'MetricName': name,
            'Dimensions': [{'Name': key, 'Value': value} for key, value in dimensions.items()]},
            'Period': 60, 'Stat': stat}}
        for index, (_, namespace, name, dimensions, stat) in enumerate(metrics)]
    response = cw.get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end,
                                  ScanBy='TimestampDescending')
    if response.get('NextToken'):
        raise RuntimeError('METRIC_PAGINATION_REQUIRED: reduzca --minutes')
    data = {item['Id']: item['Values'] for item in response['MetricDataResults']}
    result = {}
    for index, (label, _, _, _, stat) in enumerate(metrics):
        values = data.get(f'm{index}', [])
        result[label] = {'data_points': len(values), 'maximum': max(values) if values else None,
                         'sum': sum(values) if values and stat == 'Sum' else None}
    alarms = cw.describe_alarms(AlarmNames=[outputs['PasosBacklogAlarmName'],
        outputs['PasosDLQAlarmName'], outputs['OutboxLagAlarmName']])['MetricAlarms']
    print(json.dumps({'minutes': args.minutes, 'metrics': result,
        'alarms': {alarm['AlarmName']: alarm['StateValue'] for alarm in alarms},
        'dashboard': outputs['ObservabilityDashboardName']}, indent=2))


if __name__ == '__main__':
    main()
