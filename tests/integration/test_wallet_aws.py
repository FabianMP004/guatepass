"""Opt-in AWS concurrency evidence with isolated synthetic users, not Diego's balance.

Writes identifiable fixtures to existing dev/demo tables and invokes the deployed
Cobrar Lambda (no credentials printed). Keeps fixtures/events for evidence; never
resets real balances. Outbox publisher may generate simulated notifications.
"""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest

pytestmark = pytest.mark.skipif(os.getenv('RUN_WALLET_AWS') != '1',
    reason='Requiere RUN_WALLET_AWS=1 y stack dev/demo desplegado')


def test_real_concurrent_debit_and_duplicate_delivery():
    import boto3
    from common.models import make_event
    from common.money import calcular_tarifa
    region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    session = boto3.Session(region_name=region)
    info = session.client('cloudformation').describe_stacks(
        StackName=os.getenv('GP_STACK', 'guatepass-dev'))['Stacks'][0]
    params = {p['ParameterKey']: p['ParameterValue'] for p in info['Parameters']}
    assert params['Environment'] in ('dev', 'demo')
    outputs = {o['OutputKey']: o['OutputValue'] for o in info['Outputs']}
    db = session.resource('dynamodb')
    users, tolls, passes, moves, pending, outbox = [db.Table(outputs[k + 'TableName']) for k in
        ('Usuarios', 'Peajes', 'Pasos', 'Movimientos', 'Pendientes', 'Outbox')]
    run = 'IT7-' + uuid.uuid4().hex
    placa, peaje = run, run
    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    users.put_item(Item={'placa': placa, 'sub': run, 'carnet': outputs['OwnerCarnetValue'],
        'saldo_centavos': 500, 'version': 0, 'nombre': 'Diego - fixture aislado', 'prueba_id': run},
        ConditionExpression='attribute_not_exists(placa)')
    tolls.put_item(Item={'peaje_id': peaje, 'tarifa_base_centavos': 500, 'prueba_id': run},
                   ConditionExpression='attribute_not_exists(peaje_id)')
    messages = []
    for i in (1, 2):
        pid = run + '-' + str(i)
        passes.put_item(Item={'paso_id': pid, 'placa': placa, 'peaje_id': peaje, 'tag_id': None,
            'estado': 'REGISTRADO', 'version': 1, 'modalidad': None, 'monto_centavos': None,
            'recibido_en': stamp, 'timestamp': stamp, 'fecha_local': stamp[:10], 'prueba_id': run},
            ConditionExpression='attribute_not_exists(paso_id)')
        event = make_event('paso.registrado', pid, 1, {'paso_id': pid, 'placa': placa,
            'peaje_id': peaje, 'timestamp': stamp, 'tag_id': None}, run, stamp, outputs['OwnerCarnetValue'])
        messages.append({'Records': [{'messageId': pid, 'body': json.dumps(event)}]})

    def invoke(payload):
        client = boto3.Session(region_name=region).client('lambda')
        response = client.invoke(FunctionName=outputs['CobrarFunctionName'],
                                 InvocationType='RequestResponse', Payload=json.dumps(payload).encode())
        result = json.loads(response['Payload'].read())
        assert not response.get('FunctionError'), result
        assert result == {'batchItemFailures': []}, result

    print('Prueba aislada:', run, flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(invoke, messages))
    rows = [passes.get_item(Key={'paso_id': run + '-' + str(i)}, ConsistentRead=True)['Item'] for i in (1, 2)]
    assert sorted(r['estado'] for r in rows) == ['COBRADO', 'PENDIENTE']
    before = users.get_item(Key={'placa': placa}, ConsistentRead=True)['Item']
    assert before['saldo_centavos'] == 0 and before['version'] == 2
    paid = next(r for r in rows if r['estado'] == 'COBRADO')
    movement = moves.get_item(Key={'movimiento_id': paid['cobro_id']}, ConsistentRead=True)['Item']
    assert movement['monto_centavos'] == calcular_tarifa(500, 'APP')
    debt = next(r for r in rows if r['estado'] == 'PENDIENTE')
    assert pending.get_item(Key={'placa': placa, 'orden': stamp + '#' + debt['paso_id']}, ConsistentRead=True)['Item']['vence_en'] == debt['vence_en']
    # Replay original deliveries concurrently. No financial fields from events
    # are trusted, and the same pass cannot become pending after COBRADO.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(invoke, messages * 2))
    assert users.get_item(Key={'placa': placa}, ConsistentRead=True)['Item'] == before
    for row in rows:
        assert passes.get_item(Key={'paso_id': row['paso_id']}, ConsistentRead=True)['Item'] == row
    from common.models import stable_id
    assert not moves.get_item(Key={'movimiento_id': stable_id('cobro', debt['paso_id'])}, ConsistentRead=True).get('Item')
    eid = stable_id('evento', 'paso.cobrado', paid['paso_id'], int(paid['version']))
    assert json.loads(outbox.get_item(Key={'evento_id': eid}, ConsistentRead=True)['Item']['detail'])['saldo_centavos'] == 0
    print('CONFIRMADO: saldo Q0, un cobro Q5, un pendiente, duplicados sin otro descuento.', flush=True)
