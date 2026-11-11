"""Send CP2 camera notices after TAG associations in the portal; never prints the API key."""
import argparse
from datetime import datetime,timezone
import json
import time
from urllib.request import Request,urlopen
import boto3

p=argparse.ArgumentParser();p.add_argument('--stack',default='guatepass-dev');p.add_argument('--region',default='us-east-1');p.add_argument('--placa',choices=['P-123ABC','P-666YZA'],required=True);args=p.parse_args()
session=boto3.Session(region_name=args.region)
o={x['OutputKey']:x['OutputValue'] for x in session.client('cloudformation').describe_stacks(StackName=args.stack)['Stacks'][0]['Outputs']}
db=session.resource('dynamodb')
user=db.Table(o['UsuariosTableName']).get_item(Key={'placa':args.placa},ConsistentRead=True)['Item']
expected='TAG-005' if args.placa=='P-123ABC' else 'TAG-003'
tag=db.Table(o['TagsTableName']).get_item(Key={'tag_id':expected},ConsistentRead=True).get('Item')
if not tag or tag.get('activo') is not True or tag.get('placa')!=args.placa or user.get('tag_id')!=expected:
    raise SystemExit('Primero asociar el TAG correcto desde el portal y comprobar que está activo.')
if args.placa=='P-666YZA' and int(user['saldo_centavos'])!=0:
    raise SystemExit('Andrés necesita saldo inicial Q0 para demostrar el pendiente y el saldo final Q15.50.')
key=session.client('apigateway').get_api_key(apiKey=o['WebhookApiKeyId'],includeValue=True)['value']
notice={'placa':args.placa,'peaje_id':'PEAJE_ZONA10','tag_id':expected,
 'timestamp':datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')}
r=Request(o['ApiUrl'].rstrip('/')+'/webhook/toll',method='POST',
 data=json.dumps(notice).encode(),headers={'Content-Type':'application/json','x-api-key':key})
with urlopen(r,timeout=30) as res:
    if res.status!=202:raise RuntimeError('La cámara no aceptó el paso')
    paso_id=json.load(res)['data']['paso_id']
print('paso_id:',paso_id,'placa:',args.placa,'TAG:',expected,flush=True)
for _ in range(15):
    paso=db.Table(o['PasosTableName']).get_item(Key={'paso_id':paso_id},ConsistentRead=True)['Item']
    if paso['estado']!='REGISTRADO':
        print('estado:',paso['estado'],'monto_centavos:',paso.get('monto_centavos'),'modalidad:',paso.get('modalidad'))
        break
    time.sleep(2)
else:raise SystemExit('El paso sigue REGISTRADO: revisar cola-pasos, consumidor y Outbox.')
if args.placa=='P-123ABC' and (paso['estado']!='COBRADO' or int(paso['monto_centavos'])!=450):
    raise SystemExit('Juan debe tener un cobro Q4.50 con TAG activo.')
if args.placa=='P-666YZA' and (paso['estado']!='PENDIENTE' or int(paso['monto_centavos'])!=450):
    raise SystemExit('Andrés debe quedar pendiente Q4.50 antes de la recarga.')
