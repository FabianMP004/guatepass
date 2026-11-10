"""Own-driver tag lifecycle. Does not write wallet amounts or erase history."""
import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from common import dynamo as d
from common.auth import AuthError, require_owner
from common.events import outbox_item
from common.http import response
from common.models import make_event


class TagConflict(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def authorized_user(db, placa, claims):
    user = d.get(db, 'USUARIOS', {'placa': placa})
    if not user or user.get('sub') != claims['sub']:
        raise AuthError()
    if not isinstance(user.get('carnet'), str) or not user['carnet'].strip():
        raise TagConflict('USER_IDENTITY_INCOMPLETE')
    if claims.get('custom:carnet') and claims['custom:carnet'] != user['carnet']:
        raise AuthError('Renueve su sesión.')
    return user


def tag_event(kind, placa, version, tag_id, carnet, request_id):
    return outbox_item(make_event(kind, 'usuario:' + placa, version,
        {'placa': placa, 'tag_id': tag_id, 'activo': kind == 'TagAsociado'},
        request_id, datetime.now(timezone.utc).isoformat(), carnet))


def change(db, user, claims, method, tag_id, request_id, delete_target):
    placa = user['placa']
    current_id = user.get('tag_id')
    current = d.get(db, 'TAGS', {'tag_id': current_id}) if current_id else None
    if method == 'DELETE':
        signature = tuple((current or {}).get(k) for k in ('placa', 'activo', 'version'))
        if not delete_target:
            # Initialize only after successful reads. Pin the association's
            # generation too: deleting X must not delete a later re-association X.
            delete_target.update(tag_id=current_id, signature=signature,
                                 result_id=current_id or user.get('ultimo_tag_id'))
        elif current_id != delete_target['tag_id'] or signature != delete_target['signature']:
            return {'tag_id': delete_target['result_id'], 'activo': False}
    if method == 'GET':
        active = current and current.get('activo') is True and current.get('placa') == placa
        return {'tag': {'tag_id': current_id, 'activo': True} if active else None}
    version = user.get('version')
    if type(version) is not int or version < 0:
        raise ValueError('INVALID_USER_VERSION')
    conditions = d.guard(user, ['version', 'sub', 'carnet', 'tag_id'])
    update = {'TableName': d.table('USUARIOS'), 'Key': d.encode({'placa': placa}), **conditions}
    update['ExpressionAttributeNames'].update({'#newv': 'version', '#tag': 'tag_id', '#last': 'ultimo_tag_id'})
    update['ExpressionAttributeValues'][':newv'] = d.encode({'v': version + 1})['v']
    items = []
    if method == 'POST':
        target = current if tag_id == current_id else d.get(db, 'TAGS', {'tag_id': tag_id})
        if target and target.get('activo') is True and target.get('placa') != placa:
            raise TagConflict('TAG_IN_USE')
        if current and current.get('activo') is True and current.get('placa') == placa and current_id != tag_id:
            raise TagConflict('ACTIVE_TAG_EXISTS')
        if current_id == tag_id and target and target.get('activo') is True and target.get('placa') == placa:
            return {'tag_id': tag_id, 'placa': placa, 'activo': True}
        updated_tag = dict(target or {}, tag_id=tag_id, placa=placa, activo=True,
                           version=int((target or {}).get('version', 0)) + 1)
        # If replacing a stale/inactive link, ensure it stayed inactive while
        # reading. A compliant writer also increments the user's version.
        if current_id and current_id != tag_id:
            items.append(d.check('TAGS', {'tag_id': current_id}, current,
                                 ['tag_id', 'placa', 'activo', 'version']))
        items.append(d.put('TAGS', updated_tag, target, ['tag_id', 'placa', 'activo', 'version']))
        update['UpdateExpression'] = 'SET #newv = :newv, #tag = :tag REMOVE #last'
        update['ExpressionAttributeValues'][':tag'] = d.encode({'v': tag_id})['v']
        kind = 'TagAsociado'
        result = {'tag_id': tag_id, 'placa': placa, 'activo': True}
    else:
        if not current_id:
            return {'tag_id': user.get('ultimo_tag_id'), 'activo': False}
        tag_id = current_id
        if current and current.get('placa') == placa:
            updated_tag = dict(current, activo=False, version=int(current.get('version', 0)) + 1)
            items.append(d.put('TAGS', updated_tag, current, ['tag_id', 'placa', 'activo', 'version']))
        else:
            # Clear stale user pointer, never deactivate a different owner's tag.
            items.append(d.check('TAGS', {'tag_id': tag_id}, current, ['tag_id', 'placa', 'activo', 'version']))
        update['UpdateExpression'] = 'SET #newv = :newv, #last = :tag REMOVE #tag'
        update['ExpressionAttributeValues'][':tag'] = d.encode({'v': tag_id})['v']
        kind = 'TagDesactivado'
        result = {'tag_id': tag_id, 'activo': False}
    items.extend([{'Update': update}, d.put('OUTBOX', tag_event(
        kind, placa, version + 1, tag_id, user['carnet'], request_id))])
    db.transact_write_items(TransactItems=items)
    return result


def handler(event, context):
    try:
        placa = (event.get('pathParameters') or {}).get('placa')
        claims = require_owner(event, placa, os.environ['CLIENT_ID'])
        method = event.get('httpMethod')
        if method not in ('POST', 'GET', 'DELETE'):
            return response(event, 405, message='Método no permitido.', code='METHOD_NOT_ALLOWED')
        tag_id = None
        if method == 'POST':
            raw = event.get('body') or ''
            if event.get('isBase64Encoded'):
                raw = base64.b64decode(raw, validate=True).decode('utf-8')
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {'tag_id'}:
                raise ValueError('INVALID_BODY')
            tag_id = body['tag_id']
            if not isinstance(tag_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', tag_id):
                raise ValueError('INVALID_TAG')
        db = d.client()
        delete_target = {}
        for attempt in range(6):
            try:
                user = authorized_user(db, placa, claims)
                result = change(db, user, claims, method, tag_id,
                                event.get('requestContext', {}).get('requestId', 'tag'), delete_target)
                return response(event, 200, result)
            except Exception as exc:
                if not d.retryable(exc) or attempt == 5:
                    raise
                d.backoff(attempt)
    except AuthError as exc:
        return response(event, exc.status, message=str(exc), code='UNAUTHORIZED' if exc.status == 401 else 'FORBIDDEN')
    except TagConflict as exc:
        return response(event, 409, message='Asociación no disponible o identidad incompleta.', code=exc.code)
    except (ValueError, TypeError, UnicodeError):
        return response(event, 400, message='Petición inválida.', code='VALIDATION_ERROR')
    except Exception as exc:
        logging.error('tag operation failed error_type=%s', type(exc).__name__)
        return response(event, 503, message='Reintente la misma operación.', code='TEMPORARILY_UNAVAILABLE')
