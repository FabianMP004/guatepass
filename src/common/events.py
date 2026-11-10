import json
from common.models import validate_event

def outbox_item(event):
    validate_event(event)
    d = event['detail']
    return {'evento_id': d['evento_id'], 'source': event['source'],
            'detail-type': event['detail-type'], 'detail': json.dumps(d, ensure_ascii=False),
            'enviado': False, 'creado_en': d['occurred_at'],
            'pendiente_shard': f"PENDING#{int(d['evento_id'][-2:], 16) % 4:02d}"}

def from_outbox(row):
    event = {'source': row['source'], 'detail-type': row['detail-type'],
             'detail': json.loads(row['detail'])}
    validate_event(event)
    if event['detail']['evento_id'] != row['evento_id']:
        raise ValueError('INVALID_EVENT_ID')
    return event
