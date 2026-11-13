"""Publish the static SPA and invalidate HTML/config plus versioned assets."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid
import boto3


def publish(stack,region):
    subprocess.run([sys.executable,'scripts/export_config.py','--stack',stack,'--region',region],check=True)
    session=boto3.Session(region_name=region)
    outputs={x['OutputKey']:x['OutputValue'] for x in session.client('cloudformation').describe_stacks(StackName=stack)['Stacks'][0]['Outputs']}
    files={'index.html':'text/html; charset=utf-8','config.json':'application/json',
        'app.js':'text/javascript; charset=utf-8','live.mjs':'text/javascript; charset=utf-8',
        'styles.css':'text/css; charset=utf-8',
        'backoffice/index.html':'text/html; charset=utf-8',
        'backoffice/app.js':'text/javascript; charset=utf-8'}
    s3=session.client('s3')
    for name,mime in files.items():
        s3.put_object(Bucket=outputs['PortalBucketName'],Key=name,Body=(Path('portales')/name).read_bytes(),ContentType=mime,CacheControl='no-cache')
    cdn=session.client('cloudfront')
    invalidation=cdn.create_invalidation(DistributionId=outputs['PortalDistributionId'],
        InvalidationBatch={'Paths':{'Quantity':len(files)+1,'Items':['/'+name for name in files]+['/backoffice/']},
            'CallerReference':str(uuid.uuid4())})['Invalidation']['Id']
    print(json.dumps({'portal_url':outputs['PortalUrl'],'invalidation_id':invalidation,'files':list(files)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stack',default='guatepass-dev');parser.add_argument('--region',default='us-east-1');args=parser.parse_args()
    publish(args.stack,args.region)
