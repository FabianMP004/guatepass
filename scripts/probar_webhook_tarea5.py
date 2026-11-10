"""Live smoke test. Creates one durable test pass; never prints API key."""
import argparse
import json
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
import boto3

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--placa', required=True)
    parser.add_argument('--peaje', required=True)
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    stack = session.client('cloudformation').describe_stacks(StackName=args.stack)['Stacks'][0]
    outputs = {o['OutputKey']:o['OutputValue'] for o in stack['Outputs']}
    api_key = session.client('apigateway').get_api_key(apiKey=outputs['WebhookApiKeyId'],includeValue=True)['value']
    aviso = {'placa':args.placa,'peaje_id':args.peaje,
             'timestamp':datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z'), 'tag_id':None}
    identifiers = []
    for attempt in range(3):
        start = time.perf_counter()
        request = Request(outputs['ApiUrl'].rstrip('/')+'/webhook/toll',data=json.dumps(aviso).encode(),
                          headers={'Content-Type':'application/json','x-api-key':api_key},method='POST')
        with urlopen(request,timeout=35) as result:
            status = result.status
            body = json.load(result)
        elapsed = time.perf_counter()-start
        assert status == 202, body
        identifiers.append(body['data']['paso_id'])
        print(json.dumps({'intento':attempt+1,'segundos':round(elapsed,4),
                          'revisar_latencia':elapsed>=1,'request_id':body['request_id'], 'paso_id':identifiers[-1]}))
    assert len(set(identifiers)) == 1
    table = session.resource('dynamodb').Table(outputs['PasosTableName'])
    row = table.get_item(Key={'paso_id':identifiers[0]},ConsistentRead=True)['Item']
    assert row['timestamp'] == aviso['timestamp']
    from hashlib import sha256
    raw = json.dumps(['evento','PasoRegistrado',identifiers[0],1],ensure_ascii=False,separators=(',',':'))
    event_id = 'evento_'+sha256(raw.encode()).hexdigest()
    outbox = session.resource('dynamodb').Table(outputs['OutboxTableName'])
    for _ in range(20):
        event = outbox.get_item(Key={'evento_id':event_id},ConsistentRead=True).get('Item',{})
        if event.get('enviado'):
            print(json.dumps({'outbox_enviado':True,'evento_id':event_id}))
            return
        time.sleep(2)
    raise RuntimeError('Paso persistido, publicación aún no confirmada; revisar logs y recuperación.')

if __name__ == '__main__':
    main()
