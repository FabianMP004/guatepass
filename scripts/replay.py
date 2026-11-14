"""Replay stored events and verify existing charge/refund have no second effect."""
import argparse
import json
import time

import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--cobro-id', required=True)
    parser.add_argument('--disputa-id', required=True)
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    outputs = {row['OutputKey']: row['OutputValue'] for row in session.client('cloudformation')
        .describe_stacks(StackName=args.stack)['Stacks'][0]['Outputs']}
    db = session.resource('dynamodb')
    movement = db.Table(outputs['MovimientosTableName'])
    payment = movement.get_item(Key={'movimiento_id': args.cobro_id}, ConsistentRead=True)['Item']
    if payment.get('tipo') != 'COBRO' or not payment.get('paso_id'):
        raise ValueError('COBRO_NO_VALIDO')
    disputes = db.Table(outputs['DisputasTableName'])
    ref = disputes.get_item(Key={'cobro_id': 'REF#' + args.disputa_id}, ConsistentRead=True)['Item']
    dispute = disputes.get_item(Key={'cobro_id': ref['target_cobro_id']}, ConsistentRead=True)['Item']
    if dispute['cobro_id'] != args.cobro_id or dispute['estado'] != 'APROBADA':
        raise ValueError('DISPUTA_APROBADA_DE_OTRO_COBRO_REQUERIDA')
    refund_id = dispute['devolucion_id']
    def snapshot():
        return (db.Table(outputs['UsuariosTableName']).get_item(
                    Key={'placa': payment['placa']}, ConsistentRead=True)['Item']['saldo_centavos'],
                movement.get_item(Key={'movimiento_id': args.cobro_id}, ConsistentRead=True)['Item'],
                movement.get_item(Key={'movimiento_id': refund_id}, ConsistentRead=True)['Item'])
    before = snapshot()
    wanted = {('paso.registrado', payment['paso_id']),
              ('paso.cobrado', payment['paso_id']),
              ('disputa.resuelta', args.disputa_id)}
    found, cursor = {}, None
    outbox = db.Table(outputs['OutboxTableName'])
    while True:
        kwargs = {'ConsistentRead': True}
        if cursor:kwargs['ExclusiveStartKey'] = cursor
        page = outbox.scan(**kwargs)
        for row in page['Items']:
            detail = json.loads(row['detail'])
            key = (row['detail-type'], detail.get('aggregate_id'))
            if key in wanted:
                found[key] = row
        cursor = page.get('LastEvaluatedKey')
        if not cursor:break
    if wanted - found.keys():
        raise ValueError('EVENTOS_FALTANTES: ' + repr(wanted - found.keys()))
    bus = session.client('events')
    for key in sorted(wanted):
        row = found[key]
        result = bus.put_events(Entries=[{'EventBusName': outputs['EventBusName'],
            'Source': row['source'], 'DetailType': row['detail-type'], 'Detail': row['detail']}])
        if result['FailedEntryCount']:
            raise RuntimeError('REPLAY_PUBLICATION_FAILED')
    time.sleep(12)
    after = snapshot()
    print(json.dumps({'eventos_reproducidos': sorted(kind for kind, _ in wanted),
        'saldo_antes_centavos': int(before[0]), 'saldo_despues_centavos': int(after[0]),
        'cobro_sin_cambios': before[1] == after[1],
        'devolucion_sin_cambios': before[2] == after[2]}, ensure_ascii=False))
    if before != after:
        raise SystemExit('REPLAY_CAMBIO_EFECTOS')


if __name__ == '__main__':
    main()
