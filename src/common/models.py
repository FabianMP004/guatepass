"""GuatePass contracts v2 plus immutable v1 compatibility. Pure helpers."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from common.catalogo import SOURCES, validate_payload

TRANSITIONS = {
    'REGISTRADO': {'PENDIENTE','COBRADO','NO_REGISTRADO'},
    'PENDIENTE': {'COBRADO','FACTURADO'},
    'NO_REGISTRADO': {'FACTURADO','NO_IDENTIFICADO'},
    'COBRADO': set(), 'FACTURADO': set(), 'NO_IDENTIFICADO': set(),
}
EVENTS = {'PasoRegistrado','PasoPendiente','PasoNoRegistrado','PasoNoIdentificado',
          'CobroRealizado','RecargaAplicada','FacturaEmitida','TagAsociado','TagDesactivado',
          'DisputaAbierta','DisputaRechazada','DevolucionRealizada','RecaudacionCalculada'}

def stable_id(kind, *parts):
    raw=json.dumps([kind,*parts],ensure_ascii=False,separators=(',',':'))
    return kind+'_'+sha256(raw.encode('utf-8')).hexdigest()

def utc_timestamp(value):
    if not isinstance(value,str): raise ValueError('INVALID_TIMESTAMP')
    try: dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc: raise ValueError('INVALID_TIMESTAMP') from exc
    if dt.tzinfo is None: raise ValueError('TIMEZONE_REQUIRED')
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')

def paso_id(placa, peaje_id, timestamp):
    if not isinstance(placa,str) or not placa.strip(): raise ValueError('INVALID_PLACA')
    if not isinstance(peaje_id,str) or not peaje_id.strip(): raise ValueError('INVALID_PEAJE')
    return stable_id('paso',placa.strip().upper(),peaje_id.strip(),utc_timestamp(timestamp))

def cents(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):
        raise ValueError('INVALID_AMOUNT')
    try: amount=Decimal(str(value))
    except InvalidOperation as exc: raise ValueError('INVALID_AMOUNT') from exc
    if not amount.is_finite() or amount<=0 or amount>Decimal('999999999.99'):
        raise ValueError('INVALID_AMOUNT')
    scaled=amount*100
    if scaled!=scaled.to_integral_value(): raise ValueError('INVALID_AMOUNT_PRECISION')
    return int(scaled)

def transition(current, target):
    if current not in TRANSITIONS or target not in TRANSITIONS[current]:
        raise ValueError('INVALID_TRANSITION')
    return target

def replay(amount_centavos, stored):
    """Compare an already persisted record. Caller owns scope and atomic storage."""
    if amount_centavos!=stored['monto_centavos']: raise ValueError('IDEMPOTENCY_CONFLICT')
    return deepcopy(stored['response'])

def validate_event(event):
    if not isinstance(event,dict): raise ValueError('INVALID_EVENT')
    kind=event.get('detail-type')
    canonical=kind in SOURCES
    if not canonical and kind not in EVENTS: raise ValueError('INVALID_EVENT_TYPE')
    expected_source=SOURCES[kind] if canonical else 'guatepass'
    if event.get('source')!=expected_source: raise ValueError('INVALID_SOURCE')
    d=event.get('detail')
    if not isinstance(d,dict): raise ValueError('INVALID_DETAIL')
    if d.get('grupo')!='guatepass': raise ValueError('INVALID_GROUP')
    for field in ('carnet','evento_id','aggregate_id','request_id','occurred_at'):
        if not isinstance(d.get(field),str) or not d[field].strip(): raise ValueError('MISSING_'+field.upper())
    if type(d.get('schema_version')) is not int or d['schema_version']!=(2 if canonical else 1): raise ValueError('INVALID_VERSION')
    if type(d.get('aggregate_version')) is not int or d['aggregate_version']<1: raise ValueError('INVALID_AGGREGATE_VERSION')
    utc_timestamp(d['occurred_at'])
    expected=stable_id('evento',event['detail-type'],d['aggregate_id'],d['aggregate_version'])
    if d['evento_id']!=expected: raise ValueError('INVALID_EVENT_ID')
    if canonical: validate_payload(kind,d)
    return event

def make_event(event_type, aggregate_id, version, payload, request_id, occurred_at, owner_carnet, *, sub=None, assignments=None):
    """sub and assignments MUST come from validated identity and server storage."""
    carnet=(assignments or {}).get(sub,owner_carnet) if sub else owner_carnet
    detail=deepcopy(payload)
    detail.update(grupo='guatepass',carnet=carnet,schema_version=2 if event_type in SOURCES else 1,
        evento_id=stable_id('evento',event_type,aggregate_id,version),
        aggregate_id=aggregate_id,aggregate_version=version,
        request_id=request_id,occurred_at=utc_timestamp(occurred_at))
    return validate_event({'source':SOURCES.get(event_type,'guatepass'),'detail-type':event_type,'detail':detail})
