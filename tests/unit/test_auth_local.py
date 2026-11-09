import unittest
from src.common.auth import AuthError, require_role, require_owner, operator_toll
from src.common.deadlines import deadline
from datetime import datetime, timezone

def event(role='conductor', **claims):
    return {'requestContext':{'authorizer':{'claims':{'sub':'u1','token_use':'id','aud':'client','cognito:groups':role,**claims}}}}

class AuthTests(unittest.TestCase):
    def test_missing_context(self):
        with self.assertRaises(AuthError): require_role({},'conductor','client')
    def test_own_plate(self):
        self.assertEqual(require_owner(event(**{'custom:placa':'P1'}),'P1','client')['sub'],'u1')
    def test_other_plate(self):
        with self.assertRaises(AuthError): require_owner(event(**{'custom:placa':'P1'}),'P2','client')
    def test_wrong_role(self):
        with self.assertRaises(AuthError): require_owner(event('operador',**{'custom:placa':'P1'}),'P1','client')
    def test_access_token(self):
        with self.assertRaises(AuthError): require_role(event(token_use='access'),'conductor','client')
    def test_other_client(self):
        with self.assertRaises(AuthError): require_role(event(aud='evil'),'conductor','client')
    def test_operator_cannot_choose_toll(self):
        e=event('operador',**{'custom:peaje_id':'T1'});e['queryStringParameters']={'peaje_id':'T2'}
        with self.assertRaises(AuthError): operator_toll(e,'client')
    def test_operator_own_toll(self):
        self.assertEqual(operator_toll(event('[operador]',**{'custom:peaje_id':'T1'}),'client'),'T1')
    def test_groups_json_array(self):
        require_role(event('["conductor"]'),'conductor','client')
    def test_deadline_reads_ssm_each_time(self):
        class SSM:
            value='120'
            def get_parameter(self, **kw): return {'Parameter':{'Value':self.value}}
        s=SSM();now=datetime(2026,11,9,12,tzinfo=timezone.utc)
        self.assertEqual(deadline(s,'/recarga',now),'2026-11-09T12:02:00Z')
        s.value='86400'
        self.assertEqual(deadline(s,'/recarga',now),'2026-11-10T12:00:00Z')
