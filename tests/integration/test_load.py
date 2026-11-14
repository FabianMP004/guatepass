"""Load modes, retry stability, and durable reconciliation without external writes."""
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import boto3
from moto import mock_aws

from scripts.simulate import make_notices, post, percentiles
from scripts.reconcile import reconcile
from common.models import paso_id, stable_id


def test_modes_preserve_notice_identity_and_load_window():
    plates = ['P-123ABC', 'P-456DEF']
    single = make_notices('single', 9, 60, plates, 'PEAJE_ZONA10', None)
    duplicate = make_notices('duplicate', 9, 60, plates, 'PEAJE_ZONA10', None)
    concurrent = make_notices('concurrent', 3, 60, ['P-123ABC'], 'PEAJE_ZONA10', None)
    load = make_notices('load', 500, 60, plates, 'PEAJE_ZONA10', None)
    assert len(single) == 1
    assert len(duplicate) == 3 and duplicate[0] == duplicate[1] == duplicate[2]
    assert len({row['timestamp'] for row in concurrent}) == 3
    assert len({(row['placa'], row['peaje_id'], row['timestamp']) for row in load}) == 500
    first = datetime.fromisoformat(load[0]['timestamp'].replace('Z', '+00:00'))
    last = datetime.fromisoformat(load[-1]['timestamp'].replace('Z', '+00:00'))
    assert 59 <= (last-first).total_seconds() < 60
    assert percentiles([100, 1000, 2000])['over_1000ms'] == 2


def test_transient_retries_original_notice_without_exposing_camera_key():
    sent = []
    class Reply:
        status = 202
        def __enter__(self):return self
        def __exit__(self, *args):return None
        def read(self):return b'{"data":{"paso_id":"paso_one"},"request_id":"req1"}'
    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data))
        if len(sent) == 1:
            raise URLError('intermitente')
        return Reply()
    notice = {'placa': 'P-123ABC', 'peaje_id': 'PEAJE_ZONA10',
              'timestamp': '2026-11-13T10:00:00.000001Z'}
    with patch('scripts.simulate.urlopen', side_effect=fake_urlopen), \
         patch('scripts.simulate.time.sleep'):
        attempts = post('https://example.com', 'secret-camera-key', notice)
    assert sent == [notice, notice]
    assert len(attempts) == 2 and attempts[-1]['status'] == 202
    assert 'secret-camera-key' not in json.dumps(attempts)


def test_reconcile_detects_duplicate_charge_and_negative_wallet(tmp_path):
    with mock_aws():
        db = boto3.resource('dynamodb', region_name='us-east-1')
        tables = {}
        for name, key in [('PasosTableName', 'paso_id'), ('MovimientosTableName', 'movimiento_id'),
                          ('FacturasTableName', 'factura_id'), ('UsuariosTableName', 'placa')]:
            tables[name] = db.create_table(TableName=name, BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName': key, 'AttributeType': 'S'}],
                KeySchema=[{'AttributeName': key, 'KeyType': 'HASH'}])
        notice = {'placa': 'P-123ABC', 'peaje_id': 'PEAJE_ZONA10',
                  'timestamp': '2026-11-13T10:00:00.000001Z'}
        pid = paso_id(**notice)
        tables['PasosTableName'].put_item(Item={'paso_id': pid, 'estado': 'COBRADO'})
        tables['MovimientosTableName'].put_item(Item={'movimiento_id': stable_id('cobro', pid),
            'paso_id': pid, 'tipo': 'COBRO', 'cobro_id': stable_id('cobro', pid)})
        tables['UsuariosTableName'].put_item(Item={'placa': 'P-123ABC', 'saldo_centavos': 0})
        manifest = {'schema': 1, 'stack': 'dev', 'region': 'us-east-1', 'run_id': 'r1',
                    'entries': [{'notice': notice, 'attempts': [{'status': 202, 'paso_id': pid}]}]}
        class Session:
            def client(self, service):
                if service == 'cloudformation':
                    class CF:
                        def describe_stacks(self, StackName):
                            return {'Stacks': [{'Outputs': [{'OutputKey': name, 'OutputValue': name}
                                for name in tables]}]}
                    return CF()
                return boto3.client(service, region_name='us-east-1')
            def resource(self, service):
                return boto3.resource(service, region_name='us-east-1')
        good = reconcile(manifest, Session(), 0)
        assert good['pasos_faltantes'] == good['dobles_efectos'] == good['saldos_negativos'] == 0
        tables['MovimientosTableName'].put_item(Item={'movimiento_id': 'otro-cobro',
            'paso_id': pid, 'tipo': 'COBRO'})
        tables['UsuariosTableName'].put_item(Item={'placa': 'P-123ABC', 'saldo_centavos': -1})
        broken = reconcile(manifest, Session(), 0)
        assert broken['dobles_efectos'] > 0 and broken['saldos_negativos'] == 1
        assert {row['problema'] for row in broken['diferencias']} >= {
            'COBROS_INCORRECTOS', 'SALDO_NEGATIVO'}
