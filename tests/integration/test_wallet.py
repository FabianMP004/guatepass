"""Local integration: DynamoDB transactions in Moto, no real account writes.

Concurrency is also exercised against AWS by test_wallet_aws.py: Moto alone
does not certify DynamoDB transaction isolation.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from common import money
from common.models import make_event
from pasos.cobrar import cobrar_paso, handler
from usuarios.tags import handler as tags_handler


@pytest.fixture
def wallet(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    monkeypatch.setenv('OWNER_CARNET', '20230223')
    monkeypatch.setenv('CLIENT_ID', 'client')
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://example.com')
    monkeypatch.setenv('RECARGA_TIMEOUT_PARAMETER', '/test/timeout')
    with mock_aws():
        db = boto3.resource('dynamodb')
        tables = {}
        for name, keys in {
            'USUARIOS': ['placa'], 'PEAJES': ['peaje_id'], 'TAGS': ['tag_id'],
            'PASOS': ['paso_id'], 'MOVIMIENTOS': ['movimiento_id'],
            'OUTBOX': ['evento_id'], 'PENDIENTES': ['placa', 'orden'],
        }.items():
            monkeypatch.setenv(name + '_TABLE', name.lower())
            tables[name] = db.create_table(
                TableName=name.lower(), BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName': k, 'AttributeType': 'S'} for k in keys],
                KeySchema=[{'AttributeName': k, 'KeyType': 'HASH' if i == 0 else 'RANGE'}
                           for i, k in enumerate(keys)])
        boto3.client('ssm').put_parameter(Name='/test/timeout', Value='86400', Type='String')
        tables['PEAJES'].put_item(Item={'peaje_id': 'PEAJE_ZONA10', 'tarifa_base_centavos': 500})
        for placa in ('P-444STU', 'P-456DEF'):
            tables['USUARIOS'].put_item(Item={'placa': placa, 'sub': 'sub-' + placa,
                'carnet': '20230223', 'version': 0, 'saldo_centavos': 500})
        yield tables


def put_pass(tables, identifier='p1', placa='P-444STU', tag=None):
    tables['PASOS'].put_item(Item={'paso_id': identifier, 'placa': placa,
        'peaje_id': 'PEAJE_ZONA10', 'tag_id': tag, 'estado': 'REGISTRADO',
        'version': 1, 'modalidad': None, 'monto_centavos': None,
        'timestamp': '2026-11-10T12:00:00.000000Z',
        'recibido_en': '2026-11-10T12:00:00.000000Z', 'fecha_local': '2026-11-10'})
    return identifier


def get(tables, name, **key):
    return tables[name].get_item(Key=key, ConsistentRead=True).get('Item')


def count(tables, name):
    return tables[name].scan(ConsistentRead=True)['Count']


def api(method, placa='P-444STU', body=None, **claims):
    return {'httpMethod': method, 'pathParameters': {'placa': placa},
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'requestId': 'req', 'authorizer': {'claims': {
            'sub': 'sub-' + placa, 'custom:placa': placa, 'custom:carnet': '20230223',
            'cognito:groups': 'conductor', 'token_use': 'id', 'aud': 'client', **claims}}}}


def test_duplicate_one_debit_and_snapshot(wallet):
    put_pass(wallet)
    assert cobrar_paso('p1') == 'COBRADO'
    assert cobrar_paso('p1') == 'SIN_CAMBIO'
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0
    assert count(wallet, 'MOVIMIENTOS') == 1
    events = wallet['OUTBOX'].scan()['Items']
    assert len(events) == 1
    d = json.loads(events[0]['detail'])
    assert d['monto_centavos'] == 500 and d['saldo_centavos'] == 0
    assert d['carnet'] == '20230223'


def test_two_steps_diego_five_quetzales(wallet):
    put_pass(wallet, 'p1'); put_pass(wallet, 'p2')
    results = [cobrar_paso(p) for p in ('p1', 'p2')]
    assert results == ['COBRADO', 'PENDIENTE']
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0
    assert count(wallet, 'MOVIMIENTOS') == 1
    assert count(wallet, 'PENDIENTES') == 1
    assert get(wallet, 'USUARIOS', placa='P-444STU')['version'] == 2


def test_refund_event_pays_pending_and_preserves_refund_movement(wallet, monkeypatch):
    from common.events import from_outbox
    from common.models import stable_id
    from disputas.abrir import abrir_disputa
    from disputas.resolver import decidir_disputa

    db = boto3.resource('dynamodb')
    for name, keys in {'DISPUTAS': ['cobro_id'], 'ESPERAS': ['espera_id']}.items():
        monkeypatch.setenv(name + '_TABLE', name.lower())
        wallet[name] = db.create_table(
            TableName=name.lower(), BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[{'AttributeName': key, 'AttributeType': 'S'} for key in keys],
            KeySchema=[{'AttributeName': key, 'KeyType': 'HASH'} for key in keys])
    monkeypatch.setenv('DISPUTA_TIMEOUT_PARAMETER', '/test/timeout')

    put_pass(wallet, 'pago-original')
    put_pass(wallet, 'paso-pendiente')
    assert cobrar_paso('pago-original') == 'COBRADO'
    assert cobrar_paso('paso-pendiente') == 'PENDIENTE'
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0

    cobro_id = stable_id('cobro', 'pago-original')
    dispute = abrir_disputa(cobro_id, 'Cobro incorrecto',
        {'placa': 'P-444STU', 'sub': 'sub-P-444STU'})
    result = decidir_disputa(dispute['disputa_id'], 'APROBAR', 'Cobro duplicado',
        {'role': 'atencion', 'sub': 'agente', 'carnet': '20230223'})
    refund_id = result['devolucion_id']
    refund_before = get(wallet, 'MOVIMIENTOS', movimiento_id=refund_id)
    assert refund_before['tipo'] == 'DEVOLUCION'
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 500

    refund_event = next(from_outbox(item) for item in wallet['OUTBOX'].scan()['Items']
                        if item['detail-type'] == 'saldo.devuelto')
    assert handler({'Records': [{'body': json.dumps(refund_event),
        'messageId': 'refund-event'}]}, None) == {'batchItemFailures': []}
    assert get(wallet, 'PASOS', paso_id='paso-pendiente')['estado'] == 'COBRADO'
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0
    assert get(wallet, 'MOVIMIENTOS', movimiento_id=refund_id) == refund_before
    assert count(wallet, 'MOVIMIENTOS') == 3


def test_concurrent_distinct_steps(wallet):
    put_pass(wallet, 'p1'); put_pass(wallet, 'p2')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(cobrar_paso, ('p1', 'p2')))
    assert sorted(results) == ['COBRADO', 'PENDIENTE']
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0
    assert count(wallet, 'MOVIMIENTOS') == 1


def test_concurrent_same_pass(wallet):
    put_pass(wallet)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(cobrar_paso, ['p1', 'p1']))
    assert sorted(results) == ['COBRADO', 'SIN_CAMBIO']
    assert count(wallet, 'MOVIMIENTOS') == 1


@pytest.mark.parametrize('exists,active,owner,expected', [
    (True, True, 'P-444STU', 450), (True, True, 'P-456DEF', 500),
    (True, False, 'P-444STU', 500), (False, False, '', 500),
])
def test_tag_requires_real_active_owner(wallet, exists, active, owner, expected):
    if exists:
        wallet['TAGS'].put_item(Item={'tag_id': 'TAG-X', 'placa': owner, 'activo': active})
    put_pass(wallet, tag='TAG-X')
    assert cobrar_paso('p1') == 'COBRADO'
    assert get(wallet, 'PASOS', paso_id='p1')['monto_centavos'] == expected


def test_pending_repeat_preserves_deadline_and_snapshot(wallet):
    wallet['USUARIOS'].update_item(Key={'placa': 'P-444STU'},
        UpdateExpression='SET saldo_centavos=:zero', ExpressionAttributeValues={':zero': 0})
    wallet['TAGS'].put_item(Item={'tag_id': 'TAG-X', 'placa': 'P-444STU', 'activo': True})
    put_pass(wallet, tag='TAG-X')
    assert cobrar_paso('p1') == 'PENDIENTE'
    before = get(wallet, 'PASOS', paso_id='p1')
    wallet['TAGS'].update_item(Key={'tag_id': 'TAG-X'}, UpdateExpression='SET activo=:off',
        ExpressionAttributeValues={':off': False})
    wallet['PEAJES'].update_item(Key={'peaje_id': 'PEAJE_ZONA10'},
        UpdateExpression='SET tarifa_base_centavos=:v', ExpressionAttributeValues={':v': 999})
    boto3.client('ssm').put_parameter(Name='/test/timeout', Value='120', Type='String', Overwrite=True)
    assert cobrar_paso('p1') == 'SIN_CAMBIO'
    assert get(wallet, 'PASOS', paso_id='p1') == before
    assert count(wallet, 'PENDIENTES') == 1 and count(wallet, 'OUTBOX') == 1
    # Test fixture funding only; production recargas belong to the next task.
    wallet['USUARIOS'].update_item(Key={'placa': 'P-444STU'},
        UpdateExpression='SET saldo_centavos=:v', ExpressionAttributeValues={':v': 500})
    # Original pass delivery is idempotent even if later funding exists.
    # Ordered pending collection is the recarga task, not a pass replay.
    assert cobrar_paso('p1') == 'SIN_CAMBIO'
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 500
    assert get(wallet, 'PASOS', paso_id='p1') == before


def test_unregistered_does_not_debit_or_call_external(wallet):
    put_pass(wallet, placa='P-999ZZZ', tag='TAG-UNKNOWN')
    assert cobrar_paso('p1') == 'NO_REGISTRADO'
    assert cobrar_paso('p1') == 'SIN_CAMBIO'
    paso = get(wallet, 'PASOS', paso_id='p1')
    assert paso['modalidad'] == 'NO_REGISTRADO' and paso['monto_centavos'] == 750
    assert count(wallet, 'MOVIMIENTOS') == count(wallet, 'PENDIENTES') == 0
    assert wallet['OUTBOX'].scan()['Items'][0]['detail-type'] == 'paso.no_registrado'


def test_recoverable_conflict_not_insufficient_funds(wallet):
    put_pass(wallet)
    real = money.transact
    calls = []
    def transient(db, items):
        calls.append(1)
        if len(calls) == 1:
            raise ClientError({'Error': {'Code': 'TransactionCanceledException'},
                'CancellationReasons': [{'Code': 'TransactionConflict'}]}, 'TransactWriteItems')
        return real(db, items)
    with patch.object(money, 'transact', side_effect=transient):
        assert cobrar_paso('p1') == 'COBRADO'
    assert count(wallet, 'PENDIENTES') == 0


def test_validation_failure_is_not_pending(wallet):
    put_pass(wallet)
    with patch.object(money, 'transact', side_effect=ClientError({
        'Error': {'Code': 'TransactionCanceledException'},
        'CancellationReasons': [{'Code': 'ValidationError'}]}, 'TransactWriteItems')):
        with pytest.raises(ClientError): cobrar_paso('p1')
    assert count(wallet, 'PENDIENTES') == count(wallet, 'MOVIMIENTOS') == 0


def test_lost_success_response_does_not_double_charge(wallet):
    put_pass(wallet)
    real = money.transact
    def lose_response(db, items):
        result = real(db, items)
        if any(i.get('Put', {}).get('TableName') == 'movimientos' for i in items):
            raise TimeoutError('response lost after commit')
        return result
    with patch.object(money, 'transact', side_effect=lose_response):
        with pytest.raises(TimeoutError): cobrar_paso('p1')
    assert cobrar_paso('p1') == 'SIN_CAMBIO'
    assert count(wallet, 'MOVIMIENTOS') == 1
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0


def test_known_user_missing_carnet_fails_without_debit(wallet):
    wallet['USUARIOS'].update_item(Key={'placa': 'P-444STU'}, UpdateExpression='REMOVE carnet')
    put_pass(wallet)
    with pytest.raises(ValueError, match='IDENTITY'): cobrar_paso('p1')
    assert count(wallet, 'MOVIMIENTOS') == 0


def test_classification_survives_crash_then_tag_change(wallet):
    wallet['TAGS'].put_item(Item={'tag_id': 'TAG-X', 'placa': 'P-444STU', 'activo': True})
    put_pass(wallet, tag='TAG-X')
    with patch.object(money, 'transition', side_effect=TimeoutError('crash after classification')):
        with pytest.raises(TimeoutError): cobrar_paso('p1')
    assert get(wallet, 'PASOS', paso_id='p1')['monto_centavos'] == 450
    wallet['TAGS'].update_item(Key={'tag_id': 'TAG-X'}, UpdateExpression='SET activo=:off',
        ExpressionAttributeValues={':off': False})
    assert cobrar_paso('p1') == 'COBRADO'
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 50


def test_technical_failure_exhaustion_does_not_create_pending(wallet):
    put_pass(wallet)
    error = ClientError({'Error': {'Code': 'TransactionCanceledException'},
        'CancellationReasons': [{'Code': 'TransactionConflict'}]}, 'TransactWriteItems')
    with patch.object(money, 'transact', side_effect=error), patch('common.dynamo.backoff'):
        with pytest.raises(ClientError): cobrar_paso('p1')
    assert count(wallet, 'PENDIENTES') == count(wallet, 'MOVIMIENTOS') == 0


def test_partial_batch(wallet):
    put_pass(wallet)
    event = make_event('paso.registrado', 'p1', 1, {
        'paso_id': 'p1', 'placa': 'P-444STU', 'peaje_id': 'PEAJE_ZONA10',
        'timestamp': '2026-11-10T12:00:00Z', 'tag_id': None},
        'req', '2026-11-10T12:00:00Z', '20230223')
    result = handler({'Records': [
        {'messageId': 'ok', 'body': json.dumps(event)},
        {'messageId': 'bad', 'body': '{}'}]}, None)
    assert result == {'batchItemFailures': [{'itemIdentifier': 'bad'}]}
    assert count(wallet, 'MOVIMIENTOS') == 1


def test_publisher_mark_failure_republication_has_single_charge(wallet):
    from common.events import outbox_item
    from eventos import publicar
    put_pass(wallet)
    event = make_event('paso.registrado', 'p1', 1, {
        'paso_id': 'p1', 'placa': 'P-444STU', 'peaje_id': 'PEAJE_ZONA10',
        'timestamp': '2026-11-10T12:00:00Z', 'tag_id': None},
        'req', '2026-11-10T12:00:00Z', '20230223')
    row = outbox_item(event)
    wallet['OUTBOX'].put_item(Item=row)
    def accepted(events):
        for payload in events:
            response = handler({'Records': [{'messageId': 'delivery', 'body': json.dumps(payload)}]}, None)
            assert response == {'batchItemFailures': []}
        return []
    from unittest.mock import Mock
    resource = Mock(); resource.Table.return_value = wallet['OUTBOX']
    with patch.object(publicar, 'publicar', side_effect=accepted), patch.object(publicar.boto3, 'resource', return_value=resource):
        with patch.object(wallet['OUTBOX'], 'update_item', side_effect=RuntimeError('mark unavailable')):
            assert publicar.publish_ids([row['evento_id']]) == [row['evento_id']]
        assert publicar.publish_ids([row['evento_id']]) == []
    assert count(wallet, 'MOVIMIENTOS') == 1
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0


def test_new_classification_conflict_rechecks_tag(wallet):
    wallet['TAGS'].put_item(Item={'tag_id': 'TAG-X', 'placa': 'P-444STU', 'activo': True})
    put_pass(wallet, tag='TAG-X')
    real = money.transact
    first = True
    def deactivate_before_commit(db, items):
        nonlocal first
        if first:
            first = False
            wallet['TAGS'].update_item(Key={'tag_id': 'TAG-X'}, UpdateExpression='SET activo=:off',
                ExpressionAttributeValues={':off': False})
        return real(db, items)
    with patch.object(money, 'transact', side_effect=deactivate_before_commit):
        assert cobrar_paso('p1') == 'COBRADO'
    assert get(wallet, 'PASOS', paso_id='p1')['modalidad'] == 'APP'
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 0


def test_tag_association_and_deactivation_repeatable(wallet):
    first = tags_handler(api('POST', body={'tag_id': 'TAG-X'}), None)
    assert first['statusCode'] == 200
    version = get(wallet, 'USUARIOS', placa='P-444STU')['version']
    assert tags_handler(api('POST', body={'tag_id': 'TAG-X'}), None)['statusCode'] == 200
    assert get(wallet, 'USUARIOS', placa='P-444STU')['version'] == version
    assert tags_handler(api('GET'), None)['statusCode'] == 200
    stolen = tags_handler(api('POST', 'P-456DEF', {'tag_id': 'TAG-X'}), None)
    assert stolen['statusCode'] == 409 and json.loads(stolen['body'])['codigo'] == 'TAG_IN_USE'
    second = tags_handler(api('POST', body={'tag_id': 'TAG-Y'}), None)
    assert second['statusCode'] == 409
    assert tags_handler(api('DELETE'), None)['statusCode'] == 200
    assert tags_handler(api('DELETE'), None)['statusCode'] == 200
    assert json.loads(tags_handler(api('GET'), None)['body'])['data'] == {'tag': None}
    assert get(wallet, 'TAGS', tag_id='TAG-X')['activo'] is False
    assert get(wallet, 'USUARIOS', placa='P-444STU')['saldo_centavos'] == 500
    assert count(wallet, 'OUTBOX') == 2  # one association, one deactivation


def test_tag_unauthorized_and_body_fields(wallet):
    assert tags_handler(api('POST', body={'tag_id': 'TAG-X'}, **{'custom:placa': 'other'}), None)['statusCode'] == 403
    assert tags_handler(api('GET', **{'sub': 'other'}), None)['statusCode'] == 403
    assert tags_handler(api('GET', **{'cognito:groups': 'operador'}), None)['statusCode'] == 403
    assert tags_handler(api('POST', body={'tag_id': 'TAG-X', 'placa': 'other'}), None)['statusCode'] == 400


@pytest.mark.parametrize('new_tag', ['TAG-X', 'TAG-Y'])
def test_delete_retry_cannot_deactivate_new_association(wallet, new_tag):
    assert tags_handler(api('POST', body={'tag_id': 'TAG-X'}), None)['statusCode'] == 200
    db = boto3.client('dynamodb')
    original = db.transact_write_items
    first = True
    def ambiguous(**kwargs):
        nonlocal first
        result = original(**kwargs)
        if first:
            first = False
            # Commit succeeded, another request associates Y, then response lost.
            assert tags_handler(api('POST', body={'tag_id': new_tag}), None)['statusCode'] == 200
            raise ClientError({'Error': {'Code': 'InternalServerError'}}, 'TransactWriteItems')
        return result
    from usuarios import tags
    with patch.object(db, 'transact_write_items', side_effect=ambiguous):
        # Only the outer handler gets this client; nested association must use
        # a separate client so its transaction is not intercepted recursively.
        actual = tags.d.client
        calls = [db]
        def choose_client():
            return calls.pop() if calls else actual()
        with patch.object(tags.d, 'client', side_effect=choose_client):
            result = tags_handler(api('DELETE'), None)
    assert result['statusCode'] == 200
    assert json.loads(result['body'])['data']['tag_id'] == 'TAG-X'
    assert get(wallet, 'TAGS', tag_id=new_tag)['activo'] is True
    assert get(wallet, 'USUARIOS', placa='P-444STU')['tag_id'] == new_tag


def test_delete_retries_failed_initial_read(wallet):
    from usuarios import tags
    assert tags_handler(api('POST', body={'tag_id': 'TAG-X'}), None)['statusCode'] == 200
    original = tags.d.get
    first = True
    def read(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise ClientError({'Error': {'Code': 'InternalServerError'}}, 'GetItem')
        return original(*args, **kwargs)
    with patch.object(tags.d, 'get', side_effect=read):
        assert tags_handler(api('DELETE'), None)['statusCode'] == 200
    assert get(wallet, 'TAGS', tag_id='TAG-X')['activo'] is False
