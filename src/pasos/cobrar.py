"""SQS adapter. Never trusts money or mode from an event; reads stored pass."""
import json
import logging
from common import money
from common import dynamo as d
from common.models import validate_event
from common.catalogo import LEGACY
from pasos.pendientes import cobrar_pendientes
from common.observability import emit


def cobrar_paso(paso_id: str) -> str:
    return money.cobrar_paso(paso_id)


def handler(event, context):
    failed = []
    for record in event['Records']:
        message = None
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
                state = cobrar_paso(detail['paso_id'])
                emit(paso_id=detail['paso_id'], evento_id=detail.get('evento_id'),
                     request_id=detail.get('request_id'), estado=state)
            elif kind in ('paso.pendiente', 'saldo.recargado', 'saldo.devuelto'):
                cobrar_pendientes(detail['placa'], context=context)
                emit(paso_id=detail.get('paso_id'), evento_id=detail.get('evento_id'),
                     request_id=detail.get('request_id'), estado='DRENAJE_COMPLETO')
            else:
                raise ValueError('UNSUPPORTED_WALLET_EVENT')
        except Exception as exc:
            detail = message.get('detail') if isinstance(message, dict) else None
            detail = detail if isinstance(detail, dict) else {}
            response = getattr(exc, 'response', {})
            code = response.get('Error', {}).get('Code') or type(exc).__name__
            reasons = [reason.get('Code') for reason in response.get('CancellationReasons', [])
                       if reason.get('Code') not in (None, 'None')]
            emit(paso_id=detail.get('paso_id'), evento_id=detail.get('evento_id'),
                 request_id=detail.get('request_id'), error=code)
            logging.error('billing failed message_id=%s error_code=%s cancellation_codes=%s',
                          record['messageId'], code, reasons[:5])
            failed.append({'itemIdentifier': record['messageId']})
    return {'batchItemFailures': failed}
