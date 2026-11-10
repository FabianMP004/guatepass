"""SAM invariants checked locally; sam validate --lint is still required."""
import unittest
from pathlib import Path
import yaml


class InfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = yaml.safe_load((Path(__file__).resolve().parents[2] / 'template.yaml').read_text())

    def test_consumer_disabled_by_default_and_partial_failures(self):
        t = self.template
        self.assertEqual(t['Parameters']['BillingConsumerEnabled']['Default'], 'false')
        function = t['Resources']['CobrarFunction']['Properties']
        mapping = function['Events']['Messages']['Properties']
        self.assertEqual(mapping['Enabled'], {'Fn::If': ['BillingEnabled', True, False]})
        self.assertEqual(mapping['FunctionResponseTypes'], ['ReportBatchItemFailures'])
        visibility = t['Resources']['PasosQueue']['Properties']['VisibilityTimeout']
        self.assertGreaterEqual(visibility, 6 * function['Timeout'] + mapping['MaximumBatchingWindowInSeconds'])

    def test_tags_use_default_cognito_not_api_key(self):
        resources = self.template['Resources']
        self.assertEqual(resources['Api']['Properties']['Auth']['DefaultAuthorizer'], 'Cognito')
        routes = resources['TagsFunction']['Properties']['Events'].values()
        self.assertEqual({r['Properties']['Method'] for r in routes}, {'get', 'post', 'delete'})
        for route in routes:
            self.assertEqual(route['Properties']['Path'], '/users/{placa}/tag')
            self.assertNotIn('Auth', route['Properties'])

    def test_financial_writes_have_no_index_permissions(self):
        statements = self.template['Resources']['CobrarRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
        for statement in statements:
            self.assertNotIn('/index/', str(statement['Resource']))
        self.assertEqual(self.template['Resources']['CobrarLogs']['Properties']['RetentionInDays'], 14)

    def test_tags_have_no_movements_permissions(self):
        statements = self.template['Resources']['TagsRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
        self.assertNotIn('MovimientosTable', str(statements))
