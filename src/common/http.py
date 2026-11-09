import json, os

def response(event,status,data=None,message='Operación completada.',code='OK'):
    body={'codigo':code,'mensaje':message,'request_id':event.get('requestContext',{}).get('requestId','unknown')}
    if data is not None: body['data']=data
    return {'statusCode':status,'headers':{'Content-Type':'application/json','Access-Control-Allow-Origin':os.environ['ALLOWED_ORIGIN'],'Access-Control-Allow-Headers':'Content-Type,Authorization,Idempotency-Key','Access-Control-Allow-Methods':'GET,POST,DELETE,OPTIONS','Vary':'Origin'},'body':json.dumps(body,ensure_ascii=False)}
