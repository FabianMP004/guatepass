import argparse,subprocess,sys
from pathlib import Path
import boto3
p=argparse.ArgumentParser();p.add_argument('--stack',default='guatepass-dev');p.add_argument('--region',default='us-east-1');a=p.parse_args()
subprocess.run([sys.executable,'scripts/export_config.py','--stack',a.stack,'--region',a.region],check=True)
o={x['OutputKey']:x['OutputValue'] for x in boto3.client('cloudformation',region_name=a.region).describe_stacks(StackName=a.stack)['Stacks'][0]['Outputs']}
s3=boto3.client('s3',region_name=a.region)
for name,mime in [('index.html','text/html; charset=utf-8'),('config.json','application/json')]:
 s3.put_object(Bucket=o['PortalBucketName'],Key=name,Body=Path('portales',name).read_bytes(),ContentType=mime,CacheControl='no-cache')
print(o['PortalUrl'])
