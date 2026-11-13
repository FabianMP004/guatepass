"""Small-demo accounting against paginated, consistent Moto DynamoDB reads."""
import json
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
import yaml
from moto import mock_aws

from common.events import from_outbox
from eventos.notificar import notificar
from reportes.recaudacion import calcular_recaudacion, handler


@pytest.fixture
def tables(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    monkeypatch.setenv('OWNER_CARNET', '20230223')
    monkeypatch.setenv('CLIENT_ID', 'client')
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://example.com')
    with mock_aws():
        db = boto3.resource('dynamodb', region_name='us-east-1')
        tables = {}
        for name, key in {'PEAJES': 'peaje_id', 'MOVIMIENTOS': 'movimiento_id',
                          'FACTURAS': 'factura_id', 'RECAUDACION': 'recaudacion_id',
                          'OUTBOX': 'evento_id', 'NOTIFICACIONES': 'notificacion_id'}.items():
            monkeypatch.setenv(name + '_TABLE', name.lower())
            tables[name] = db.create_table(TableName=name.lower(), BillingMode='PAY_PER_REQUEST',
                AttributeDefinitions=[{'AttributeName': key, 'AttributeType': 'S'}],
                KeySchema=[{'AttributeName': key, 'KeyType': 'HASH'}])
        for toll in ('PEAJE_A', 'PEAJE_B'):
            tables['PEAJES'].put_item(Item={'peaje_id': toll, 'tarifa_base_centavos': 500})
        yield tables


def movement(tables, mid, kind, amount, when, toll):
    tables['MOVIMIENTOS'].put_item(Item={
        'movimiento_id': mid, 'tipo': kind, 'monto_centavos': amount,
        'fecha': when, 'peaje_id': toll})


class TwoItemPages:
    """Force DynamoDB pagination for every read, including consecutive empty pages."""
    def __init__(self, client):
        self.client = client

    def scan(self, **kwargs):
        return self.client.scan(Limit=2, **kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


def test_revenue_refund_invoice_recharge_and_idempotent_finance(tables):
    date = '2026-11-12'
    movement(tables, 'pago-450', 'COBRO', 450, '2026-11-12T06:00:00Z', 'PEAJE_A')
    movement(tables, 'pago-500', 'COBRO', 500, '2026-11-13T05:59:59Z', 'PEAJE_B')
    movement(tables, 'devolucion-450', 'DEVOLUCION', 450, '2026-11-13T05:59:59Z', 'PEAJE_A')
    movement(tables, 'recarga-2000', 'RECARGA', 2000, '2026-11-12T12:00:00Z', 'PEAJE_B')
    movement(tables, 'fuera-de-dia', 'COBRO', 900, '2026-11-13T06:00:00Z', 'PEAJE_A')
    tables['FACTURAS'].put_item(Item={'factura_id': 'factura-750', 'peaje_id': 'PEAJE_B',
        'monto_centavos': 750, 'emitida_en': '2026-11-12T18:00:00Z',
        'estado': 'POR_COBRAR'})

    from common import dynamo as d
    paged = TwoItemPages(d.client())
    with patch('reportes.recaudacion.d.client', return_value=paged):
        report = calcular_recaudacion(date)
        first = tables['RECAUDACION'].scan()['Items']
        repeat = calcular_recaudacion(date)
    assert report == repeat
    assert report['totales'] == {'cobros_centavos': 950, 'devoluciones_centavos': 450,
        'neto_centavos': 500, 'por_cobrar_centavos': 750}
    assert report['desde_utc'] == '2026-11-12T06:00:00Z'
    assert report['hasta_utc_exclusivo'] == '2026-11-13T06:00:00Z'
    assert {item['peaje_id']: item['neto_centavos'] for item in report['peajes']} == {
        'PEAJE_A': 0, 'PEAJE_B': 500}
    assert len(first) == tables['RECAUDACION'].scan()['Count'] == 2
    events = tables['OUTBOX'].scan()['Items']
    assert len(events) == 2
    for item in events:
        event = from_outbox(item)
        notificar(event)
        notificar(event)
    notifications = tables['NOTIFICACIONES'].scan()['Items']
    assert len(notifications) == 2
    assert {row['destinatario'] for row in notifications} == {'finanzas'}
    assert all('Reporte ' in row['mensaje'] for row in notifications)

    movement(tables, 'cobro-tardio', 'COBRO', 100, '2026-11-12T23:00:00Z', 'PEAJE_B')
    with patch('reportes.recaudacion.d.client', return_value=paged):
        updated = calcular_recaudacion(date)
    assert updated['totales']['cobros_centavos'] == 1050
    assert updated['totales']['neto_centavos'] == 600
    assert tables['RECAUDACION'].scan()['Count'] == 2
    assert tables['OUTBOX'].scan()['Count'] == 3
    assert next(row for row in updated['peajes'] if row['peaje_id'] == 'PEAJE_B')['version'] == 2


def test_admin_read_only_api_and_scheduler(tables):
    admin = {'sub': 'admin', 'token_use': 'id', 'aud': 'client',
             'cognito:groups': ['administrador']}
    event = {'httpMethod': 'GET', 'resource': '/tolls',
             'requestContext': {'authorizer': {'claims': admin}, 'requestId': 'req'}}
    assert json.loads(handler(event, None)['body'])['data']['items'][0]['peaje_id'] == 'PEAJE_A'
    event['resource'] = '/revenue/{fecha}'
    event['pathParameters'] = {'fecha': '2026-11-12'}
    assert json.loads(handler(event, None)['body'])['data']['calculado'] is False
    event['requestContext']['authorizer']['claims'] = {
        **admin, 'cognito:groups': ['conductor']}
    assert handler(event, None)['statusCode'] == 403
    event['requestContext']['authorizer']['claims'] = admin
    event['httpMethod'] = 'POST'
    assert handler(event, None)['statusCode'] == 405
    event['httpMethod'] = 'GET'
    event['pathParameters'] = {'fecha': 'not-a-date'}
    assert handler(event, None)['statusCode'] == 400

    resources = yaml.safe_load((Path(__file__).resolve().parents[2] / 'template.yaml').read_text())['Resources']
    schedule = resources['RevenueSchedule']['Properties']
    assert schedule['ScheduleExpression'] == 'cron(0 6 * * ? *)'
    assert schedule['ScheduleExpressionTimezone'] == 'America/Guatemala'
    assert schedule['FlexibleTimeWindow']['Mode'] == 'OFF'
