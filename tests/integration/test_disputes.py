"""Opt-in real AWS acceptance tests; each test needs its own existing test payment.

RUN_DISPUTES_AWS=1 GP_STACK=guatepass-dev DISPUTE_COBRO_ID=... \
 DISPUTE_DRIVER_SUB=... DISPUTE_PLACA=... DISPUTE_CARNET=... \
 python -m pytest tests/integration/test_disputes.py -v
"""
import os
import time
import boto3
import pytest
from common import dynamo as d
from disputas.abrir import abrir_disputa, DisputeConflict
from disputas.resolver import decidir_disputa, iniciar
from disputas.devolucion import aplicar_devolucion

pytestmark = pytest.mark.skipif(os.getenv('RUN_DISPUTES_AWS') != '1',
    reason='Requiere stack de prueba y cobros propios aislados')


@pytest.fixture
def setup(monkeypatch):
    output = {row['OutputKey']: row['OutputValue'] for row in
        boto3.client('cloudformation').describe_stacks(StackName=os.environ['GP_STACK'])['Stacks'][0]['Outputs']}
    for name in ('Disputas', 'Usuarios', 'Movimientos', 'Outbox', 'Esperas'):
        monkeypatch.setenv(name.upper() + '_TABLE', output[name + 'TableName'])
    monkeypatch.setenv('DISPUTA_TIMEOUT_PARAMETER', output['DisputaTimeoutParameterName'])
    monkeypatch.setenv('DISPUTA_STATE_MACHINE_ARN', output['EsperaDisputaArn'])
    monkeypatch.setenv('OWNER_CARNET', os.environ['DISPUTE_CARNET'])
    actor = {'placa': os.environ['DISPUTE_PLACA'], 'sub': os.environ['DISPUTE_DRIVER_SUB']}
    return actor


def test_own_payment_one_dispute_one_refund(setup):
    payment = os.environ['DISPUTE_COBRO_ID']
    before = d.get(d.client(), 'USUARIOS', {'placa': setup['placa']})
    try:
        opened = abrir_disputa(payment, 'Cobro en revisión', setup)
    except DisputeConflict:
        pytest.skip('El cobro de prueba ya tenía disputa: proporcionar un cobro nuevo')
    with pytest.raises(DisputeConflict):
        abrir_disputa(payment, 'Otra clave de petición', setup)
    did = opened['disputa_id']
    other = dict(setup, sub='otro-usuario')
    with pytest.raises(PermissionError):
        abrir_disputa(payment, 'Cobro ajeno', other)
    agent = {'role': 'atencion', 'sub': 'atencion-test', 'carnet': os.environ['DISPUTE_CARNET']}
    with pytest.raises(ValueError, match='INVALID_REASON'):
        decidir_disputa(did, 'RECHAZAR', '', agent)
    result = decidir_disputa(did, 'APROBAR', 'Cobro erróneo', agent)
    again = aplicar_devolucion(did)
    assert result['devolucion_id'] == again['devolucion_id']
    user = d.get(d.client(), 'USUARIOS', {'placa': setup['placa']})
    movement = d.get(d.client(), 'MOVIMIENTOS', {'movimiento_id': result['devolucion_id']})
    assert movement['tipo'] == 'DEVOLUCION'
    assert movement['saldo_centavos'] == movement['saldo_anterior_centavos'] + movement['monto_centavos']
    # A pending pass can spend the refunded balance immediately afterwards.
    assert user['version'] > before['version']
    with pytest.raises(ValueError, match='DISPUTE_ALREADY_RESOLVED'):
        decidir_disputa(did, 'RECHAZAR', 'No', agent)


def test_rejection_does_not_credit(setup):
    payment = os.getenv('DISPUTE_REJECT_COBRO_ID')
    if not payment:
        pytest.skip('Proporcionar DISPUTE_REJECT_COBRO_ID de un segundo cobro limpio')
    opened = abrir_disputa(payment, 'Validar paso', setup)
    result = decidir_disputa(opened['disputa_id'], 'RECHAZAR', 'Cobro válido',
        {'role': 'atencion', 'sub': 'atencion-test', 'carnet': os.environ['DISPUTE_CARNET']})
    assert result['estado'] == 'RECHAZADA' and result['devolucion_id'] is None


def test_expiry_approves_once(setup):
    payment = os.getenv('DISPUTE_EXPIRE_COBRO_ID')
    if not payment:
        pytest.skip('Proporcionar DISPUTE_EXPIRE_COBRO_ID y timeout de demo')
    seconds = int(boto3.client('ssm').get_parameter(Name=os.environ['DISPUTA_TIMEOUT_PARAMETER'])['Parameter']['Value'])
    if seconds > 180:
        pytest.skip('Configurar plazo de demo antes de abrir la disputa')
    opened = abrir_disputa(payment, 'Esperar vencimiento', setup)
    did = opened['disputa_id']
    execution_arn = iniciar(did)
    assert execution_arn.startswith('arn:aws:states:')
    from datetime import datetime, timedelta, timezone
    deadline = datetime.fromisoformat(opened['vence_en'].replace('Z', '+00:00')) + timedelta(seconds=90)
    db = d.client()
    while datetime.now(timezone.utc) < deadline:
        row = d.get(db, 'DISPUTAS', {'cobro_id': payment})
        status = boto3.client('stepfunctions').describe_execution(executionArn=execution_arn)['status']
        if status in ('FAILED', 'TIMED_OUT', 'ABORTED'):
            pytest.fail('El workflow terminó en ' + status)
        if status == 'SUCCEEDED' and row['estado'] == 'APROBADA':
            break
        time.sleep(2)
    else:
        pytest.fail('El workflow no aprobó la disputa a tiempo')
    assert row['resolucion'] == 'AUTOMATICA'
    assert row['resuelta_por'] == 'SISTEMA'
    assert row['carnet_decisor'] == os.environ['DISPUTE_CARNET']
    refund = aplicar_devolucion(did)
    assert refund['estado'] == 'APROBADA'
    assert refund['devolucion_id'] == row['devolucion_id']
    movement = d.get(db, 'MOVIMIENTOS', {'movimiento_id': refund['devolucion_id']})
    assert movement['tipo'] == 'DEVOLUCION' and movement['monto_centavos'] == row['monto_centavos']
    with pytest.raises(ValueError, match='DISPUTE_ALREADY_RESOLVED'):
        decidir_disputa(did, 'RECHAZAR', 'Fuera de plazo',
            {'role': 'atencion', 'sub': 'atencion-test', 'carnet': os.environ['DISPUTE_CARNET']})
