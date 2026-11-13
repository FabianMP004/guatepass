"""Invoke the deployed revenue Lambda for one explicit Guatemala calendar date."""
import argparse
import json
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from reportes.recaudacion import local_date


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--fecha', required=True, help='Fecha local en formato AAAA-MM-DD')
    args = parser.parse_args()
    local_date(args.fecha)
    session = boto3.Session(region_name=args.region)
    outputs = {row['OutputKey']: row['OutputValue'] for row in
               session.client('cloudformation').describe_stacks(StackName=args.stack)['Stacks'][0]['Outputs']}
    response = session.client('lambda').invoke(
        FunctionName=outputs['RevenueFunctionName'], InvocationType='RequestResponse',
        Payload=json.dumps({'fecha': args.fecha}).encode('utf-8'))
    result = json.loads(response['Payload'].read())
    if response.get('FunctionError') or response['StatusCode'] != 200:
        raise RuntimeError('REVENUE_INVOCATION_FAILED: ' + str(result)[:500])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
