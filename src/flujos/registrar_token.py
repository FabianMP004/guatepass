"""Stores callback token internally and closes waits already paid before registration."""
from common import dynamo as d
from flujos.completar import completar, key


def registrar_token(event,context):
    pid=event['paso_id'];token=event['token'];execution=event['execution_arn']
    if not all(isinstance(x,str) and x for x in (pid,token,execution)):
        raise ValueError('INVALID_WAIT_TOKEN')
    db=d.client();paso=d.get(db,'PASOS',{'paso_id':pid})
    if not paso:raise ValueError('PASS_NOT_FOUND')
    old=d.get(db,'ESPERAS',key(pid))
    if old and old.get('execution_arn') not in (None,execution):
        raise ValueError('WAIT_EXECUTION_MISMATCH')
    row=dict(old or {},**key(pid),paso_id=pid,tipo='RECARGA',token=token,
             execution_arn=execution,vence_en=paso.get('vence_en',''))
    # This is backend-only data. Never log token or Lambda payload.
    try:
        db.put_item(TableName=d.table('ESPERAS'),Item=d.encode(row),
            **d.guard(old,['token','execution_arn'] if old else ['espera_id']))
    except Exception as exc:
        if getattr(exc,'response',{}).get('Error',{}).get('Code')!='ConditionalCheckFailedException':raise
        latest=d.get(db,'ESPERAS',key(pid))
        if not latest or latest.get('execution_arn')!=execution:raise
    # Check after saving token: credit may have won earlier or during this write.
    if d.get(db,'PASOS',{'paso_id':pid})['estado']=='COBRADO':
        completar(pid)
    return {'registrado':True}
