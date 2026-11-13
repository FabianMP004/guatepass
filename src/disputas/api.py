"""Cognito REST entry point for dispute creation, listing and decisions."""
import base64
import json
import logging
import os
from common import dynamo as d
from common.auth import AuthError, require_role
from common.http import response
from disputas.abrir import abrir_disputa, DisputeConflict
from disputas.listar import listar_disputas
from disputas.resolver import decidir_disputa, iniciar


def _body(event):
    raw = event.get('body') or ''
    if event.get('isBase64Encoded'):
        raw = base64.b64decode(raw, validate=True).decode('utf-8')
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError('INVALID_BODY')
    return body


def _driver(event):
    claims = require_role(event, 'conductor', os.environ['CLIENT_ID'])
    placa = claims.get('custom:placa')
    if not placa:
        raise AuthError()
    user = d.get(d.client(), 'USUARIOS', {'placa': placa})
    if not user or user.get('sub') != claims['sub']:
        raise AuthError()
    return {'role': 'conductor', 'sub': claims['sub'], 'placa': placa,
            'request_id': event.get('requestContext', {}).get('requestId', 'disputa')}


def _agent(event):
    claims = require_role(event, 'atencion', os.environ['CLIENT_ID'])
    carnet = claims.get('custom:carnet')
    if not carnet:
        raise AuthError('Identidad incompleta.')
    return {'role': 'atencion', 'sub': claims['sub'], 'carnet': carnet}


def handler(event, context):
    try:
        method = event.get('httpMethod')
        path = event.get('resource') or event.get('path') or ''
        if path.endswith('/resolve') and method == 'POST':
            actor = _agent(event)
            data = _body(event)
            if set(data) != {'decision', 'motivo'}:
                raise ValueError('INVALID_BODY')
            result = decidir_disputa((event.get('pathParameters') or {}).get('id'),
                                     data['decision'], data['motivo'], actor)
            return response(event, 200, result)
        if path.endswith('/disputes') and method == 'POST':
            actor = _driver(event)
            data = _body(event)
            if set(data) != {'cobro_id', 'motivo'}:
                raise ValueError('INVALID_BODY')
            result = abrir_disputa(data['cobro_id'], data['motivo'], actor)
            # If starting fails the durable dispute is picked up by reconciliation.
            try:
                iniciar(result['disputa_id'])
            except Exception as exc:
                logging.error('dispute wait start deferred error_type=%s', type(exc).__name__)
            return response(event, 201, result)
        if path.endswith('/disputes') and method == 'GET':
            try:
                actor = _driver(event)
            except AuthError as exc:
                if exc.status == 401:
                    raise
                actor = _agent(event)
            q = event.get('queryStringParameters') or {}
            if set(q) - {'limit', 'cursor'}:
                raise ValueError('INVALID_QUERY')
            return response(event, 200, listar_disputas(actor, int(q.get('limit', 20)), q.get('cursor')))
        return response(event, 405, code='METHOD_NOT_ALLOWED', message='Método no permitido.')
    except AuthError as exc:
        return response(event, exc.status, message=str(exc), code='UNAUTHORIZED' if exc.status == 401 else 'FORBIDDEN')
    except PermissionError:
        return response(event, 403, code='FORBIDDEN', message='Acceso no permitido.')
    except DisputeConflict:
        return response(event, 409, code='DISPUTE_EXISTS', message='Ya existe una disputa para este cobro.')
    except ValueError as exc:
        code = str(exc)
        if code in ('PAYMENT_NOT_FOUND', 'DISPUTE_NOT_FOUND'):
            return response(event, 404, code=code, message='Registro no encontrado.')
        if code in ('DISPUTE_ALREADY_RESOLVED', 'DISPUTE_EXPIRED'):
            return response(event, 409, code=code, message='La disputa ya fue resuelta o venció.')
        return response(event, 400, code='VALIDATION_ERROR', message='Petición inválida.')
    except Exception as exc:
        logging.error('dispute request failed error_type=%s', type(exc).__name__)
        return response(event, 503, code='TEMPORARILY_UNAVAILABLE', message='Reintente la operación.')
