"""More than one pending step for the same wallet uses valid DynamoDB expressions."""
from unittest.mock import patch

from common import money
from pasos.cobrar import cobrar_paso
from test_wallet import wallet, put_pass, get, count


def test_third_step_with_existing_pending_uses_only_expression_values(wallet):
    for pid in ('primer-paso', 'segundo-paso', 'tercer-paso'):
        put_pass(wallet, pid)
    assert cobrar_paso('primer-paso') == 'COBRADO'
    assert cobrar_paso('segundo-paso') == 'PENDIENTE'

    original = money.transact
    checked = []

    def inspect_transaction(db, items):
        for item in items:
            if 'Update' in item and item['Update']['TableName'] == 'usuarios':
                update = item['Update']
                expression = update['ConditionExpression'] + ' ' + update['UpdateExpression']
                values = update['ExpressionAttributeValues']
                checked.append(True)
                assert all(token in expression for token in values)
                assert ':amount' not in values
        return original(db, items)

    with patch.object(money, 'transact', side_effect=inspect_transaction):
        assert cobrar_paso('tercer-paso') == 'PENDIENTE'

    assert checked
    assert get(wallet, 'PASOS', paso_id='tercer-paso')['estado'] == 'PENDIENTE'
    assert count(wallet, 'PENDIENTES') == 2
    assert count(wallet, 'MOVIMIENTOS') == 1
