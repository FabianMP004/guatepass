import unittest
from unittest.mock import patch

from disputas.devolucion import aplicar_devolucion


class FakeDB:
    def __init__(self, disputa, user, movement=None):
        self.rows = {'DISPUTAS': disputa, 'USUARIOS': user, 'MOVIMIENTOS': movement}
        self.writes = []

    def transact_write_items(self, TransactItems):
        self.writes.append(TransactItems)
        self.rows['DISPUTAS'] = next(op['Put']['Item'] for op in TransactItems
                                     if op.get('Put', {}).get('TableName') == 'DISPUTAS')
        movements = [op['Put']['Item'] for op in TransactItems
                     if op.get('Put', {}).get('TableName') == 'MOVIMIENTOS']
        if movements:
            self.rows['MOVIMIENTOS'] = movements[0]


class DevolucionTests(unittest.TestCase):
    def setUp(self):
        self.disputa = {'cobro_id': 'cobro_1', 'disputa_id': 'disputa_1',
                        'placa': 'P-123ABC', 'peaje_id': 'PEAJE_ZONA10',
                        'propietario_sub': 'driver',
                        'monto_centavos': 450, 'estado': 'ABIERTA', 'version': 1,
                        'decision': 'APROBAR', 'motivo_decision': 'Cobro incorrecto',
                        'resuelta_por': 'agente', 'carnet_decisor': '20230223',
                        'resolucion': 'MANUAL'}
        self.user = {'placa': 'P-123ABC', 'sub': 'driver', 'carnet': '20230223',
                     'saldo_centavos': 100, 'version': 4}

    def run_case(self, disputa=None, user=None, movement=None):
        db = FakeDB(disputa or dict(self.disputa), user or dict(self.user), movement)
        def get(_db, table, _key):
            if table == 'DISPUTAS' and _key['cobro_id'].startswith('REF#'):
                return {'target_cobro_id': 'cobro_1'}
            return db.rows[table]
        with patch('disputas.devolucion.d.client', return_value=db), \
             patch('disputas.devolucion.d.get', side_effect=get), \
             patch('disputas.devolucion.d.table', side_effect=lambda name: name), \
             patch('disputas.devolucion.d.encode', side_effect=lambda obj: obj), \
             patch.dict('os.environ', {'OWNER_CARNET': '20230223'}):
            return db, aplicar_devolucion('disputa_1')

    def test_approval_is_one_atomic_credit_with_events(self):
        db, result = self.run_case()
        self.assertEqual(result['estado'], 'APROBADA')
        self.assertEqual(len(db.writes), 1)
        tx = db.writes[0]
        self.assertEqual(len(tx), 5)
        self.assertEqual(db.rows['MOVIMIENTOS']['tipo'], 'DEVOLUCION')
        self.assertEqual(db.rows['MOVIMIENTOS']['saldo_centavos'], 550)
        update = next(op['Update'] for op in tx if 'Update' in op)
        self.assertEqual(update['ExpressionAttributeValues'][':amount'], 450)
        self.assertEqual(update['ExpressionAttributeValues'][':version'], 4)
        self.assertIn('#v = :version', update['ConditionExpression'])
        self.assertIn('#b = :balance', update['ConditionExpression'])
        dispute_write = next(op['Put'] for op in tx if op.get('Put', {}).get('TableName') == 'DISPUTAS')
        self.assertIn('#g0 = :g0', dispute_write['ConditionExpression'])
        self.assertEqual(db.rows['DISPUTAS']['estado'], 'APROBADA')

    def test_duplicate_approval_does_not_credit_twice(self):
        dispute = dict(self.disputa, estado='APROBADA', devolucion_id='devolucion_1')
        db, result = self.run_case(dispute)
        self.assertEqual(result['estado'], 'APROBADA')
        self.assertEqual(db.writes, [])

    def test_rejection_does_not_credit(self):
        dispute = dict(self.disputa, decision='RECHAZAR')
        db, result = self.run_case(dispute)
        self.assertEqual(result['estado'], 'RECHAZADA')
        self.assertEqual(len(db.writes), 1)
        self.assertFalse(any('Update' in item for item in db.writes[0]))

    def test_owner_mismatch_aborts_without_writes(self):
        user = dict(self.user, sub='different')
        db = FakeDB(dict(self.disputa, propietario_sub='driver'), user)
        def get(_db, table, key):
            if table == 'DISPUTAS' and key['cobro_id'].startswith('REF#'):
                return {'target_cobro_id': 'cobro_1'}
            return db.rows[table]
        with patch('disputas.devolucion.d.client', return_value=db), \
             patch('disputas.devolucion.d.get', side_effect=get), \
             patch('disputas.devolucion.d.table', side_effect=lambda name: name), \
             patch('disputas.devolucion.d.encode', side_effect=lambda obj: obj):
            with self.assertRaises(ValueError):
                aplicar_devolucion('disputa_1')
        self.assertEqual(db.writes, [])


if __name__ == '__main__':
    unittest.main()
