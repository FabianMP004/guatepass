"""Fast, dependency-light tests of the dispute decision boundary."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

try:
    import boto3
except ModuleNotFoundError:
    sys.modules['boto3'] = MagicMock()
    from disputas.abrir import abrir_disputa, DisputeConflict
    from disputas.resolver import decidir_disputa, vencer_disputa
    sys.modules.pop('boto3', None)
else:
    from disputas.abrir import abrir_disputa, DisputeConflict
    from disputas.resolver import decidir_disputa, vencer_disputa


class DisputeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.payment = {'movimiento_id': 'cobro_1', 'tipo': 'COBRO', 'placa': 'P-123ABC',
                        'peaje_id': 'PEAJE_ZONA10', 'monto_centavos': 450}
        self.user = {'placa': 'P-123ABC', 'sub': 'driver', 'carnet': '20230223'}
        self.driver = {'placa': 'P-123ABC', 'sub': 'driver'}

    def test_other_driver_cannot_open_payment(self):
        with patch('disputas.abrir.d.client'), patch('disputas.abrir.d.get',
                side_effect=[self.payment, self.user]):
            with self.assertRaises(PermissionError):
                abrir_disputa('cobro_1', 'Revisar cargo', {'placa': 'P-999XYZ', 'sub': 'other'})

    def test_invoice_is_not_disputable(self):
        with patch('disputas.abrir.d.client'), patch('disputas.abrir.d.get',
                return_value={'tipo': 'FACTURA'}):
            with self.assertRaisesRegex(ValueError, 'PAYMENT_NOT_FOUND'):
                abrir_disputa('cobro_1', 'Revisar cargo', self.driver)

    def test_duplicate_payment_stays_unique_across_requests(self):
        with patch('disputas.abrir.d.client'), patch('disputas.abrir.d.get',
                side_effect=[self.payment, self.user, {'disputa_id': 'existing'}]):
            with self.assertRaises(DisputeConflict):
                abrir_disputa('cobro_1', 'Otra solicitud', self.driver)

    def test_open_writes_payment_check_reference_and_outbox_together(self):
        db = MagicMock()
        def get(_db, table, _key):
            return {'MOVIMIENTOS': self.payment, 'USUARIOS': self.user, 'DISPUTAS': None}[table]
        with patch('disputas.abrir.d.client', return_value=db), \
             patch('disputas.abrir.d.get', side_effect=get), \
             patch('disputas.abrir.d.table', side_effect=lambda n: n), \
             patch('disputas.abrir.d.encode', side_effect=lambda v: v), \
             patch.dict('os.environ', {'DISPUTA_TIMEOUT_PARAMETER': 'test'}), \
             patch('disputas.abrir.deadline', return_value='2030-01-01T00:00:00Z'):
            result = abrir_disputa('cobro_1', 'Revisar cargo', self.driver)
        tx = db.transact_write_items.call_args.kwargs['TransactItems']
        self.assertEqual(result['estado'], 'ABIERTA')
        self.assertEqual(len(tx), 5)
        self.assertEqual(tx[3]['Put']['Item']['cobro_id'], 'REF#' + result['disputa_id'])

    def test_rejection_needs_reason(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_REASON'):
            decidir_disputa('disputa_1', 'RECHAZAR', ' ',
                {'role': 'atencion', 'sub': 'agent', 'carnet': '20230223'})

    def test_timeout_keeps_earlier_manual_decision(self):
        row = {'estado': 'ABIERTA', 'decision': 'RECHAZAR', 'disputa_id': 'disputa_1',
               'vence_en': (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}
        with patch('disputas.resolver.d.client'), patch('disputas.resolver._load', return_value=row), \
             patch('disputas.resolver.aplicar_devolucion', return_value={'estado': 'RECHAZADA'}) as apply, \
             patch('disputas.resolver.completar'):
            self.assertEqual(vencer_disputa('disputa_1'), 'RECHAZADA')
            apply.assert_called_once()

    def test_completion_clears_expired_callback_after_refund(self):
        from disputas.resolver import completar
        class TaskTimedOut(Exception):
            pass
        class TaskDoesNotExist(Exception):
            pass
        class InvalidToken(Exception):
            pass

        db = MagicMock()
        sfn = MagicMock()
        sfn.exceptions = SimpleNamespace(TaskTimedOut=TaskTimedOut,
            TaskDoesNotExist=TaskDoesNotExist, InvalidToken=InvalidToken)
        sfn.send_task_success.side_effect = TaskTimedOut('token vencido')
        wait = {'espera_id': 'DISPUTA#disputa_1', 'token': 'expired-token'}
        with patch('disputas.resolver.d.client', return_value=db), \
             patch('disputas.resolver.d.get', return_value=wait), \
             patch('disputas.resolver._load', return_value={'estado': 'APROBADA'}), \
             patch('disputas.resolver.boto3.client', return_value=sfn), \
             patch('disputas.resolver.d.table', return_value='esperas'), \
             patch('disputas.resolver.d.encode', side_effect=lambda v: v), \
             patch('disputas.resolver.d.guard', return_value={}):
            self.assertEqual(completar('disputa_1'), 'COMPLETADA')
        db.delete_item.assert_called_once()


if __name__ == '__main__':
    unittest.main()
