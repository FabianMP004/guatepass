"""Conductor's own POST /users/{placa}/topup, only request amount is trusted."""
import base64
import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation
from common.auth import AuthError, require_owner
from common.http import response
from common.money import MAX_TOPUP_CENTAVOS, apply_topup


def amount_to_cents(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
        raise ValueError('INVALID_AMOUNT')
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError('INVALID_AMOUNT') from None
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError('INVALID_AMOUNT')
    cents = amount * 100
    if cents > MAX_TOPUP_CENTAVOS or cents != cents.to_integral_value():
        raise ValueError('INVALID_AMOUNT')
    return int(cents)


def recargar(placa: str, monto_centavos: int, clave: str, actor: dict) -> dict:
    return apply_topup(placa, monto_centavos, clave, actor)


def handler(event, context):
    try:
        placa = (event.get('pathParameters') or {}).get('placa')
        actor = require_owner(event, placa, os.environ['CLIENT_ID'])
        headers = {k.lower(): v for k, v in (event.get('headers') or {}).items()}
        clave = headers.get('idempotency-key')
        if not isinstance(clave, str) or not clave:
            return response(event, 400, message='Idempotency-Key requerido.', code='IDEMPOTENCY_KEY_REQUIRED')
        body = event.get('body') or ''
        if event.get('isBase64Encoded'):
            body = base64.b64decode(body, validate=True).decode('utf-8')
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != {'monto'}:
            raise ValueError('INVALID_BODY')
        actor = dict(actor, request_id=event.get('requestContext', {}).get('requestId', 'topup'))
        result = recargar(placa, amount_to_cents(payload['monto']), clave, actor)
        return response(event, 200, result)
    except AuthError as exc:
        return response(event, exc.status, message=str(exc),
                        code='FORBIDDEN' if exc.status == 403 else 'UNAUTHORIZED')
    except ValueError as exc:
        if str(exc) == 'IDEMPOTENCY_CONFLICT':
            return response(event, 409, message='Clave reutilizada con otro monto.', code='IDEMPOTENCY_CONFLICT')
        return response(event, 400, message='Solicitud inválida.', code='VALIDATION_ERROR')
    except Exception as exc:
        logging.error('topup failed error_type=%s', type(exc).__name__)
        return response(event, 503, message='Reintente con la misma clave.', code='TEMPORARILY_UNAVAILABLE')
