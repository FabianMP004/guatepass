"""Persist simulated notifications atomically. Never sends email/SMS."""
import json
import logging
import os
from hashlib import sha256
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError
from common.models import validate_event
from common.catalogo import LEGACY, NOTIFIABLE, validate_payload

def presentation(evento):
    validate_event(evento)
    kind = LEGACY.get(evento['detail-type'], evento['detail-type'])
    if kind not in NOTIFIABLE:
        raise ValueError('NOT_A_NOTIFICATION_EVENT')
    detail = dict(evento['detail'])
    if evento['detail-type'] == 'DisputaRechazada':
        detail['decision'] = 'RECHAZAR'
    validate_payload(kind, detail)
    recipient = 'finanzas' if kind == 'recaudacion.calculada' else 'placa:' + detail['placa']
    amount = detail.get('monto_centavos')
    money = f'Q{amount//100}.{amount%100:02d}' if amount is not None else ''
    texts = {
        'paso.cobrado': f"Cobro de {money} por paso {detail.get('paso_id')}.",
        'paso.pendiente': f"Paso {detail.get('paso_id')} pendiente por {money}; vence {detail.get('vence_en')}.",
        'paso.facturado': f"Factura {detail.get('factura_id')} por {money}, concepto {detail.get('concepto')}.",
        'invitacion.solicitada': 'Le invitamos a registrarse en GuatePass.',
        'saldo.recargado': f"Recarga {detail.get('recarga_id')} de {money} aplicada.",
        'saldo.devuelto': f"Devolución {detail.get('devolucion_id')} de {money} aplicada.",
        'disputa.abierta': f"Disputa {detail.get('disputa_id')} abierta; vence {detail.get('vence_en')}.",
        'disputa.resuelta': f"Disputa {detail.get('disputa_id')}: {detail.get('decision')}. Motivo: {detail.get('motivo')}",
        'recaudacion.calculada': f"Reporte {detail.get('reporte_id')} de {detail.get('fecha')} disponible para finanzas.",
    }
    # No current balance reads: amounts and any balance displayed are event snapshots.
    text = texts[kind]
    if 'saldo_centavos' in detail:
        value = detail['saldo_centavos']
        text += f' Saldo en esa transición: Q{value//100}.{value%100:02d}.'
    return recipient, text

def notificar(evento: dict) -> None:
    recipient, text = presentation(evento)
    detail = evento['detail']
    identifier = detail['evento_id'] + '#' + recipient
    logical = {k:evento[k] for k in ('source','detail-type','detail')}
    digest = sha256(json.dumps(logical,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    table = boto3.resource('dynamodb').Table(os.environ['NOTIFICACIONES_TABLE'])

    def check_existing():
        stored = table.get_item(Key={'notificacion_id':identifier},ConsistentRead=True).get('Item')
        if stored:
            if stored.get('contenido_hash') != digest:
                raise ValueError('EVENT_ID_CONTENT_CONFLICT')
            return True
        return False

    if check_existing():
        return
    contact = detail.get('contacto') or {}
    if not contact and recipient != 'finanzas':
        user = boto3.resource('dynamodb').Table(os.environ['USUARIOS_TABLE']).get_item(
            Key={'placa':detail['placa']},ConsistentRead=True).get('Item', {})
        contact = {k:user.get(k) for k in ('email','telefono')}
    channel = 'PORTAL_SIMULADO'
    if contact.get('email'):
        channel = 'EMAIL_SIMULADO'
    elif contact.get('telefono'):
        channel = 'SMS_SIMULADO'
    item = {'notificacion_id':identifier,'evento_id':detail['evento_id'],
            'destinatario':recipient, 'mensaje':text, 'canal':channel,
            'fecha':datetime.now(timezone.utc).isoformat(),
            'evento_ocurrido_en':detail['occurred_at'], 'contenido_hash':digest,
            'source':evento['source'],'tipo':evento['detail-type'], 'simulada':True}
    try:
        table.put_item(Item=item,ConditionExpression='attribute_not_exists(notificacion_id)')
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        if not check_existing():
            raise RuntimeError('DEDUP_RECORD_MISSING') from exc

def handler(event, context):
    failed = []
    for record in event['Records']:
        try:
            notificar(json.loads(record['body']))
        except Exception as exc:
            # Avoid printing body, contacts, or sensitive exception arguments.
            logging.error('notification failed message_id=%s error_type=%s', record['messageId'], type(exc).__name__)
            failed.append({'itemIdentifier':record['messageId']})
    return {'batchItemFailures':failed}
