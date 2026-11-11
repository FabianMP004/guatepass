"""Periodic repair of expired debts and callbacks whose first send failed."""
import logging
from common import dynamo as d
from flujos.completar import completar
from flujos.iniciar import iniciar
from pasos.facturar import facturar_paso


def handler(event,context):
    db=d.client();counts={'facturados':0,'cierres':0,'inicios':0};cursor=None
    while True:
        args={'TableName':d.table('ESPERAS'),'ConsistentRead':True,
            'FilterExpression':'begins_with(#k,:p)',
            'ExpressionAttributeNames':{'#k':'espera_id'},
            'ExpressionAttributeValues':d.encode({':p':'RECARGA#'})}
        if cursor:args['ExclusiveStartKey']=cursor
        page=db.scan(**args)
        for raw in page['Items']:
            row=d.decode(raw);pid=row['paso_id']
            if context and context.get_remaining_time_in_millis()<8000:raise RuntimeError('RECONCILE_TIME_BUDGET')
            paso=d.get(db,'PASOS',{'paso_id':pid})
            if paso and paso['estado']=='FACTURADO':
                try:
                    db.delete_item(TableName=d.table('ESPERAS'),
                        Key=d.encode({'espera_id':row['espera_id']}),
                        **d.guard(row,['token','execution_arn']))
                except Exception as exc:
                    if getattr(exc,'response',{}).get('Error',{}).get('Code')!='ConditionalCheckFailedException':raise
            elif paso and paso['estado']=='COBRADO':
                if completar(pid)=='COMPLETADO':counts['cierres']+=1
        cursor=page.get('LastEvaluatedKey')
        if not cursor:break
    # The demo has a small Pasos table. Scan base table consistently: GSI
    # cannot make expiry decisions. Future scale needs a repair cursor/shards.
    cursor=None
    while True:
        args={'TableName':d.table('PASOS'),'ConsistentRead':True,
            'FilterExpression':'#st=:pending',
            'ExpressionAttributeNames':{'#st':'estado'},
            'ExpressionAttributeValues':d.encode({':pending':'PENDIENTE'})}
        if cursor:args['ExclusiveStartKey']=cursor
        page=db.scan(**args)
        for raw in page['Items']:
            if context and context.get_remaining_time_in_millis()<8000:raise RuntimeError('RECONCILE_TIME_BUDGET')
            pid=d.decode(raw)['paso_id'];state=facturar_paso(pid)
            if state=='FACTURADO':counts['facturados']+=1
            if state=='NO_VENCIDO':
                started=iniciar(pid)
                if started!='SIN_CAMBIO':counts['inicios']+=1
        cursor=page.get('LastEvaluatedKey')
        if not cursor:break
    return counts
