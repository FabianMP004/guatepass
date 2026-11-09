"""Only consume claims injected by the REST Cognito authorizer, never decode headers.
Signature/issuer/expiry verification belongs to API Gateway; these helpers enforce
application authorization after that boundary. No Function URL is exposed.
"""
import json

class AuthError(Exception):
    def __init__(self, message='Acceso no permitido.', status=403):
        super().__init__(message); self.status=status

def require_role(event, role, client_id):
    claims=event.get('requestContext',{}).get('authorizer',{}).get('claims')
    if not claims or not claims.get('sub'): raise AuthError('Identidad requerida.',401)
    if claims.get('token_use')!='id' or claims.get('aud')!=client_id: raise AuthError('Token no permitido.',401)
    groups=claims.get('cognito:groups',[])
    if isinstance(groups,str):
        try: groups=json.loads(groups)
        except (ValueError,TypeError): groups=[x.strip() for x in groups.strip('[]').split(',') if x.strip()]
    if not isinstance(groups,list) or role not in groups: raise AuthError()
    return claims

def require_owner(event, placa, client_id):
    c=require_role(event,'conductor',client_id)
    if not placa or c.get('custom:placa')!=placa: raise AuthError()
    return c

def operator_toll(event, client_id):
    c=require_role(event,'operador',client_id)
    # The public contract does not accept a toll selector, even for own toll.
    query=event.get('queryStringParameters') or {}
    multi=event.get('multiValueQueryStringParameters') or {}
    if 'peaje_id' in query or 'peaje_id' in multi or 'peaje' in query: raise AuthError()
    toll=c.get('custom:peaje_id')
    if not toll: raise AuthError()
    return toll
