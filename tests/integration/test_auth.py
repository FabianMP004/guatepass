"""Real AWS tests. Explicit opt-in; creates and deletes isolated test data only.
Run: RUN_AWS_INTEGRATION=1 GP_STACK=guatepass-dev python -m pytest tests/integration/test_auth.py -v
Never prints tokens or passwords. No deployed infrastructure is created manually.
"""
import base64,json,os,secrets,time,uuid
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest

pytestmark=pytest.mark.skipif(os.getenv('RUN_AWS_INTEGRATION')!='1',reason='Requiere stack desplegado y RUN_AWS_INTEGRATION=1')

class SafeFixture(dict):
    def __repr__(self): return '<GuatePass test context: credentials redacted>'

@pytest.fixture(scope='module')
def setup():
    import boto3
    region=os.getenv('AWS_REGION','us-east-1');session=boto3.Session(region_name=region)
    stack=os.getenv('GP_STACK','guatepass-dev')
    info=session.client('cloudformation').describe_stacks(StackName=stack)['Stacks'][0]
    o={x['OutputKey']:x['OutputValue'] for x in info['Outputs']}
    parameters={x['ParameterKey']:x['ParameterValue'] for x in info['Parameters']}
    if parameters['Environment'] not in ('dev','demo'):pytest.fail('Pruebas de datos permitidas sólo en dev/demo.')
    cog=session.client('cognito-idp');table=session.resource('dynamodb').Table(o['UsuariosTableName'])
    names=[];plates=[];tokens={};prefix='it-'+uuid.uuid4().hex[:12]
    plate=prefix.upper();toll='IT-'+uuid.uuid4().hex[:8]
    try:
        for role,attrs in [('conductor',{'custom:placa':plate}),('operador',{'custom:peaje_id':toll})]:
            name=prefix+'-'+role;password='Gp!A9'+secrets.token_urlsafe(24)
            cog.admin_create_user(UserPoolId=o['UserPoolId'],Username=name,MessageAction='SUPPRESS',UserAttributes=[{'Name':k,'Value':v} for k,v in attrs.items()]+[{'Name':'custom:carnet','Value':o['OwnerCarnetValue']}]);names.append(name)
            cog.admin_set_user_password(UserPoolId=o['UserPoolId'],Username=name,Password=password,Permanent=True)
            cog.admin_add_user_to_group(UserPoolId=o['UserPoolId'],Username=name,GroupName=role)
            auth=cog.initiate_auth(ClientId=o['UserPoolClientId'],AuthFlow='USER_PASSWORD_AUTH',AuthParameters={'USERNAME':name,'PASSWORD':password})['AuthenticationResult']
            tokens[role]=auth
            if role=='conductor':
                payload=auth['IdToken'].split('.')[1];claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
                table.put_item(Item={'placa':plate,'sub':claims['sub'],'saldo_centavos':2000,'version':1,'nit':'CF','carnet':o['OwnerCarnetValue']},ConditionExpression='attribute_not_exists(placa)');plates.append(plate)
        yield SafeFixture(outputs=o,tokens=tokens,plate=plate,toll=toll,session=session)
    finally:
        for plate in plates:table.delete_item(Key={'placa':plate})
        for name in names:cog.admin_delete_user(UserPoolId=o['UserPoolId'],Username=name)

def call(s,path,token=None,method='GET',headers=None,body=None):
    h={'Origin':s['outputs']['PortalUrl'],**(headers or {})}
    if token:h['Authorization']=token
    data=None
    if body is not None:
        data=json.dumps(body).encode('utf-8');h['Content-Type']='application/json'
    req=Request(s['outputs']['ApiUrl']+path,headers=h,method=method,data=data)
    try:
        with urlopen(req,timeout=30) as r:return r.status,dict(r.headers),json.loads(r.read() or '{}')
    except HTTPError as e:return e.code,dict(e.headers),json.loads(e.read() or '{}')

def token(s,role='conductor'):return s['tokens'][role]['IdToken']
def test_without_token_is_401(setup):assert call(setup,'/users/'+setup['plate'])[0]==401

def test_own_balance_allowed(setup):
    status,_,body=call(setup,'/users/'+setup['plate'],token(setup))
    assert status==200;assert body['data']['saldo']=='20.00'

def test_other_plate_is_403(setup):assert call(setup,'/users/OTHER-PLATE',token(setup))[0]==403

def test_wrong_role_is_403(setup):assert call(setup,'/users/'+setup['plate'],token(setup,'operador'))[0]==403

def test_access_token_rejected(setup):assert call(setup,'/users/'+setup['plate'],setup['tokens']['conductor']['AccessToken'])[0]==401

def test_operator_own_toll(setup):
    status,_,body=call(setup,'/passes',token(setup,'operador'))
    assert status==200;assert body['data']['peaje_id']==setup['toll']

def test_operator_cannot_choose_toll(setup):assert call(setup,'/passes?peaje_id=OTHER',token(setup,'operador'))[0]==403

def test_preflight_without_token_or_key(setup):
    assert call(setup,'/webhook/toll',method='OPTIONS',headers={'Access-Control-Request-Method':'POST'})[0]==200

def test_webhook_requires_key(setup):assert call(setup,'/webhook/toll',method='POST')[0]==403

def test_webhook_key_reaches_validation(setup):
    o=setup['outputs'];value=setup['session'].client('apigateway').get_api_key(apiKey=o['WebhookApiKeyId'],includeValue=True)['value']
    assert call(setup,'/webhook/toll',method='POST',headers={'x-api-key':value})[0]==400

def test_gateway_and_lambda_errors_have_cors(setup):
    for path,t in [('/users/'+setup['plate'],None),('/users/OTHER',token(setup))]:
        _,headers,_=call(setup,path,t)
        normalized={k.lower():v for k,v in headers.items()}
        assert normalized['access-control-allow-origin']==setup['outputs']['PortalUrl']

def test_custom_attributes_not_client_writable(setup):
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError) as exc:
        setup['session'].client('cognito-idp').update_user_attributes(AccessToken=setup['tokens']['conductor']['AccessToken'],UserAttributes=[{'Name':'custom:placa','Value':'OTHER'}])
    assert exc.value.response['Error']['Code']=='NotAuthorizedException'

def subscription(s,channel,role='operador'):
    import websocket
    o=s['outputs'];auth={'host':o['RealtimeHttpDomain'],'Authorization':token(s,role)}
    encoded=base64.urlsafe_b64encode(json.dumps(auth).encode()).decode().rstrip('=')
    ws=websocket.create_connection('wss://'+o['RealtimeDomain']+'/event/realtime',subprotocols=['aws-appsync-event-ws','header-'+encoded],timeout=20)
    try:
        ws.send(json.dumps({'type':'connection_init'}))
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            msg=json.loads(ws.recv())
            if msg['type']=='connection_ack':break
            if msg['type']=='connection_error':pytest.fail('No fue posible autenticar conexión realtime.')
        else:pytest.fail('No llegó connection_ack.')
        identifier=str(uuid.uuid4())
        ws.send(json.dumps({'type':'subscribe','id':identifier,'channel':channel,'authorization':auth}))
        while time.monotonic()<deadline:
            msg=json.loads(ws.recv())
            if msg.get('id')==identifier and msg['type'] in ('subscribe_success','subscribe_error'):return msg
        pytest.fail('Sin respuesta de suscripción.')
    finally:ws.close()

def test_operator_subscription_own_toll(setup):
    from eventos.tiempo_real import channel_for
    assert subscription(setup,channel_for(setup['toll']))['type']=='subscribe_success'

def test_operator_subscription_other_toll_denied(setup):assert subscription(setup,'/peajes/OTHER')['type']=='subscribe_error'

def test_operator_subscription_wildcard_denied(setup):assert subscription(setup,'/peajes/*')['type']=='subscribe_error'

def test_driver_subscription_denied(setup):
    from eventos.tiempo_real import channel_for
    assert subscription(setup,channel_for(setup['toll']),role='conductor')['type']=='subscribe_error'


def test_tag_routes_authorization_and_lifecycle(setup):
    path='/users/'+setup['plate']+'/tag'
    tag_id='IT7-'+uuid.uuid4().hex
    assert call(setup,path)[0]==401
    assert call(setup,path,token(setup,'operador'))[0]==403
    assert call(setup,'/users/OTHER-PLATE/tag',token(setup))[0]==403
    assert call(setup,path,method='OPTIONS',headers={'Access-Control-Request-Method':'POST'})[0]==200
    try:
        for _ in range(2):
            status,_,body=call(setup,path,token(setup),method='POST',body={'tag_id':tag_id})
            assert status==200,body
            assert body['data']=={'tag_id':tag_id,'placa':setup['plate'],'activo':True}
        assert call(setup,path,token(setup))[2]['data']['tag']=={'tag_id':tag_id,'activo':True}
        for _ in range(2):
            status,_,body=call(setup,path,token(setup),method='DELETE')
            assert status==200,body
            assert body['data']=={'tag_id':tag_id,'activo':False}
        assert call(setup,path,token(setup))[2]['data']=={'tag':None}
        assert call(setup,'/users/'+setup['plate'],token(setup))[2]['data']['saldo']=='20.00'
    finally:
        call(setup,path,token(setup),method='DELETE')
