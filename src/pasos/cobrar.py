"""SQS adapter. Never trusts money or mode from an event; reads stored pass."""
import json
import logging
from common import money
from common import dynamo as d
from common.models import validate_event
from common.catalogo import LEGACY
from pasos.pendientes import cobrar_pendientes


def cobrar_paso(paso_id: str) -> str:
    return money.cobrar_paso(paso_id)


def handler(event, context):
    failed = []
    for record in event['Records']:
        try:
            if context and context.get_remaining_time_in_millis() < 5000:
                raise RuntimeError('BATCH_TIME_BUDGET')
            message = json.loads(record['body'])
            if isinstance(message, dict) and message.get('internal') == 'continuar_drenaje':
                placa = message.get('placa')
                generation = message.get('generation')
                if not isinstance(placa, str) or not placa or type(generation) is not int:
                    raise ValueError('INVALID_CONTINUATION')
                saved = d.get(d.client(), 'ESPERAS', {'espera_id': 'DRENAJE#' + placa})
                if saved and saved.get('tipo') == 'DRENAJE' and saved.get('version') == generation and saved.get('placa') == placa:
                    cobrar_pendientes(placa, context=context)
                continue
            validate_event(message)
            kind = LEGACY.get(message['detail-type'], message['detail-type'])
            detail = message['detail']
            if kind == 'paso.registrado':
                cobrar_paso(detail['paso_id'])
            elif kind in ('paso.pendiente', 'saldo.recargado', 'saldo.devuelto'):
                cobrar_pendientes(detail['placa'], context=context)
            else:
                raise ValueError('UNSUPPORTED_WALLET_EVENT')
        except Exception as exc:
            logging.error('billing failed message_id=%s error_type=%s',
                          record['messageId'], type(exc).__name__)
            failed.append({'itemIdentifier': record['messageId']})
    return {'batchItemFailures': failed}
