"""Compare a simulation manifest with durable passes, charges, invoices and balances."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from common.models import paso_id


FINAL = {'COBRADO', 'PENDIENTE', 'FACTURADO', 'NO_IDENTIFICADO'}


def scan_all(table):
    args = {'ConsistentRead': True}
    result = []
    while True:
        page = table.scan(**args)
        result.extend(page.get('Items', []))
        if not page.get('LastEvaluatedKey'):
            return result
        args['ExclusiveStartKey'] = page['LastEvaluatedKey']


def passes(dynamodb, table_name, identifiers):
    results = {}
    serializer, deserializer = TypeSerializer(), TypeDeserializer()
    for offset in range(0, len(identifiers), 100):
        request = {table_name: {'Keys': [{'paso_id': serializer.serialize(value)}
                                         for value in identifiers[offset:offset+100]],
                                'ConsistentRead': True}}
        for retry in range(8):
            response = dynamodb.batch_get_item(RequestItems=request)
            for row in response.get('Responses', {}).get(table_name, []):
                item = {key: deserializer.deserialize(value) for key, value in row.items()}
                results[item['paso_id']] = item
            request = response.get('UnprocessedKeys', {})
            if not request:
                break
            time.sleep(min(0.1 * 2 ** retry, 2))
        if request:
            raise RuntimeError('UNPROCESSED_PASSES')
    return results


def reconcile(manifest, session, wait_seconds, require_invoices=False):
    if manifest.get('schema') != 1 or not isinstance(manifest.get('entries'), list):
        raise ValueError('INVALID_MANIFEST')
    outputs = {row['OutputKey']: row['OutputValue'] for row in session.client('cloudformation')
        .describe_stacks(StackName=manifest['stack'])['Stacks'][0]['Outputs']}
    db = session.resource('dynamodb')
    identifiers = sorted({paso_id(row['notice']['placa'], row['notice']['peaje_id'],
                                    row['notice']['timestamp']) for row in manifest['entries']})
    deadline = time.monotonic() + wait_seconds
    while True:
        stored = passes(session.client('dynamodb'), outputs['PasosTableName'], identifiers)
        unfinished = [key for key, row in stored.items() if row.get('estado') not in FINAL]
        missing = sorted(set(identifiers) - stored.keys())
        if not missing and not unfinished or time.monotonic() >= deadline:
            break
        time.sleep(min(3, max(0, deadline-time.monotonic())))

    movements = scan_all(db.Table(outputs['MovimientosTableName']))
    invoices = scan_all(db.Table(outputs['FacturasTableName']))
    users = scan_all(db.Table(outputs['UsuariosTableName']))
    movements_by_pass = defaultdict(list)
    invoices_by_pass = defaultdict(list)
    refund_by_charge = Counter()
    for movement in movements:
        if movement.get('paso_id') in stored:
            movements_by_pass[movement['paso_id']].append(movement)
        if movement.get('tipo') == 'DEVOLUCION':
            refund_by_charge[movement.get('cobro_id')] += 1
    for invoice in invoices:
        if invoice.get('paso_id') in stored:
            invoices_by_pass[invoice['paso_id']].append(invoice)
    differences = []
    for item in manifest['entries']:
        expected = paso_id(item['notice']['placa'], item['notice']['peaje_id'],
                           item['notice']['timestamp'])
        for attempt in item.get('attempts', []):
            if attempt.get('paso_id') and attempt['paso_id'] != expected:
                differences.append({'paso_id': expected, 'problema': 'RESPUESTA_ID_DISTINTO'})
    for key in identifiers:
        row = stored.get(key)
        if not row:
            differences.append({'paso_id': key, 'problema': 'PASO_FALTANTE'})
            continue
        state = row.get('estado')
        charges = [m for m in movements_by_pass[key] if m.get('tipo') == 'COBRO']
        refunds = [m for m in movements_by_pass[key] if m.get('tipo') == 'DEVOLUCION']
        bills = invoices_by_pass[key]
        if state not in FINAL:
            differences.append({'paso_id': key, 'problema': 'NO_CONVERGIO', 'estado': state})
        if len(charges) != (1 if state == 'COBRADO' else 0):
            differences.append({'paso_id': key, 'problema': 'COBROS_INCORRECTOS', 'cantidad': len(charges)})
        if len(bills) != (1 if state == 'FACTURADO' else 0):
            differences.append({'paso_id': key, 'problema': 'FACTURAS_INCORRECTAS', 'cantidad': len(bills)})
        if require_invoices and state in ('NO_REGISTRADO', 'PENDIENTE'):
            differences.append({'paso_id': key, 'problema': 'FACTURA_PENDIENTE', 'estado': state})
        if len(refunds) > 1 or any(refund_by_charge.get(m.get('cobro_id'), 0) > 1 for m in charges):
            differences.append({'paso_id': key, 'problema': 'DEVOLUCION_DUPLICADA'})
    negative = [row['placa'] for row in users if int(row.get('saldo_centavos', 0)) < 0]
    double_refunds = sorted(str(key) for key, count in refund_by_charge.items() if count > 1)
    for key in double_refunds:
        differences.append({'cobro_id': key, 'problema': 'DEVOLUCION_DUPLICADA_GLOBAL'})
    for plate in negative:
        differences.append({'placa': plate, 'problema': 'SALDO_NEGATIVO'})
    return {'run_id': manifest['run_id'], 'pasos_unicos': len(identifiers),
            'avisos_enviados': len(manifest['entries']),
            'intentos': sum(len(row.get('attempts', [])) for row in manifest['entries']),
            'pasos_encontrados': len(stored), 'pasos_faltantes': len(missing),
            'estados': dict(Counter(row.get('estado', 'SIN_ESTADO') for row in stored.values())),
            'dobles_efectos': sum(d['problema'] in ('COBROS_INCORRECTOS', 'FACTURAS_INCORRECTAS',
                'DEVOLUCION_DUPLICADA', 'DEVOLUCION_DUPLICADA_GLOBAL') for d in differences),
            'saldos_negativos': len(negative), 'diferencias': differences}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--wait-seconds', type=int, default=180)
    parser.add_argument('--require-invoices', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    if manifest['stack'] != args.stack or manifest['region'] != args.region:
        parser.error('El manifiesto no corresponde al stack y región indicados')
    result = reconcile(manifest, boto3.Session(region_name=args.region), args.wait_seconds,
                       args.require_invoices)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['diferencias']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
