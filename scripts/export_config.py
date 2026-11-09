"""Public browser configuration derived exclusively from CloudFormation outputs."""
import argparse,json
from pathlib import Path
import boto3
p=argparse.ArgumentParser();p.add_argument('--stack',default='guatepass-dev');p.add_argument('--region',default='us-east-1');a=p.parse_args()
s=boto3.client('cloudformation',region_name=a.region).describe_stacks(StackName=a.stack)['Stacks'][0]
o={x['OutputKey']:x['OutputValue'] for x in s['Outputs']}
config={'region':a.region,'api_url':o['ApiUrl'],'user_pool_id':o['UserPoolId'],'user_pool_client_id':o['UserPoolClientId'],'realtime_http_domain':o['RealtimeHttpDomain'],'realtime_domain':o['RealtimeDomain'],'portal_url':o['PortalUrl']}
Path('portales/config.json').write_text(json.dumps(config,indent=2)+'\n')
print('Configuración pública escrita en portales/config.json; no contiene clave de cámara.')
