"""Deploy built SAM template preserving current stack parameters.

--consumer false for initial deployment; true deliberately starts consuming
queued passes. Preview by default; --apply runs SAM with its changeset prompt.
"""
import argparse
import subprocess
from pathlib import Path
import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--consumer', choices=['true', 'false'], required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    built = Path('.aws-sam/build/template.yaml')
    if not built.is_file():
        raise SystemExit('Ejecute sam build antes de desplegar.')
    stack = boto3.client('cloudformation', region_name=args.region).describe_stacks(
        StackName=args.stack)['Stacks'][0]
    params = {p['ParameterKey']: p['ParameterValue'] for p in stack['Parameters']}
    if params.get('Environment') not in ('dev', 'demo'):
        raise SystemExit('Este asistente de despliegue permite únicamente dev/demo.')
    allowed = {'Environment', 'OwnerCarnet', 'RecargaTimeoutSeconds', 'DisputaTimeoutSeconds', 'BillingConsumerEnabled'}
    unknown = set(params) - allowed
    if unknown:
        raise SystemExit('Parámetros adicionales: revisar antes de desplegar: ' + ', '.join(sorted(unknown)))
    params['BillingConsumerEnabled'] = args.consumer
    print('Stack:', args.stack, 'Región:', args.region, 'Consumidor:', args.consumer)
    print('Se conservan Environment, OwnerCarnet y los dos timeouts actuales.')
    if args.consumer == 'true':
        print('Los avisos acumulados podrán generar cobros. No se vaciará ninguna cola.')
    if not args.apply:
        print('Vista previa. Añada --apply para ejecutar sam deploy.')
        return
    command = ['sam', 'deploy', '--template-file', str(built), '--stack-name', args.stack,
        '--region', args.region, '--capabilities', 'CAPABILITY_IAM', '--resolve-s3',
        '--parameter-overrides'] + [f'{key}={value}' for key, value in params.items()]
    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
