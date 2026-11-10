"""Compatible v1/v2 smoke: creates one new test pass and repeats twice."""
import argparse
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
import boto3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from common.models import stable_id

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stack',required=True);p.add_argument('--region',default='us-east-1')
    p.add_argument('--placa',required=True);p.add_argument('--peaje',required=True)
    args=p.parse_args();s=boto3.Session(region_name=args.region)
    o={x['OutputKey']:x['OutputValue'] for x in s.client('cloudformation').describe_stacks(StackName=args.stack)['Stacks'][0]['Outputs']}
    key=s.client('apigateway').get_api_key(apiKey=o['WebhookApiKeyId'],includeValue=True)['value']
    aviso={'placa':args.placa,'peaje_id':args.peaje,'tag_id':None,
           'timestamp':datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')}
    ids=[]
    for attempt in range(3):
        request=Request(o['ApiUrl'].rstrip('/')+'/webhook/toll',data=json.dumps(aviso).encode(),
                        headers={'Content-Type':'application/json','x-api-key':key},method='POST')
        start=time.perf_counter()
        with urlopen(request,timeout=35) as result:
            assert result.status==202
            body=json.load(result)
        elapsed=time.perf_counter()-start;ids.append(body['data']['paso_id'])
        print(json.dumps({'intento':attempt+1,'segundos':round(elapsed,4),'revisar_latencia':elapsed>=1,
                          'request_id':body['request_id'],'paso_id':ids[-1]}))
    assert len(set(ids))==1
    table=s.resource('dynamodb').Table(o['OutboxTableName'])
    for _ in range(20):
        for kind in ('paso.registrado','PasoRegistrado'):
            identifier=stable_id('evento',kind,ids[0],1)
            row=table.get_item(Key={'evento_id':identifier},ConsistentRead=True).get('Item')
            if row and row.get('enviado'):
                print(json.dumps({'outbox_enviado':True,'evento_id':identifier,'tipo':kind}));return
        time.sleep(2)
    raise RuntimeError('Paso recibido, publicación aún no confirmada.')

if __name__=='__main__':main()
