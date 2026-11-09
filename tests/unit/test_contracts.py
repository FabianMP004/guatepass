import unittest
from src.common.models import validate_event, make_event, cents, paso_id, transition, replay

class Contracts(unittest.TestCase):
    def event(self, **kw):
        return make_event('PasoRegistrado','p1',1,{'carnet':'FORGED'},'req','2026-11-06T12:00:00Z','OWNER',**kw)
    def test_missing_audit_fields(self):
        for field in ('grupo','carnet'):
            with self.subTest(field=field):
                e=self.event(); del e['detail'][field]
                with self.assertRaises(ValueError): validate_event(e)
    def test_known_user_carnet(self):
        self.assertEqual(self.event(sub='u1',assignments={'u1':'FABIAN'})['detail']['carnet'],'FABIAN')
    def test_camera_owner_carnet(self):
        self.assertEqual(self.event()['detail']['carnet'],'OWNER')
    def test_unassigned_user_owner(self):
        self.assertEqual(self.event(sub='unknown')['detail']['carnet'],'OWNER')
    def test_invalid_money(self):
        for value in (True,'NaN','Infinity','1.001','-2','0',{},None):
            with self.subTest(value=value), self.assertRaises(ValueError): cents(value)
    def test_money_exact(self): self.assertEqual(cents('20.10'),2010)
    def test_normalized_identity(self):
        self.assertEqual(paso_id('p123abc','T1','2026-11-06T12:00:00Z'),paso_id(' P123ABC ','T1','2026-11-06T06:00:00-06:00'))
    def test_distinct_pass(self):
        self.assertNotEqual(paso_id('P1','T1','2026-11-06T12:00:00Z'),paso_id('P1','T2','2026-11-06T12:00:00Z'))
    def test_paid_cannot_be_pending(self):
        with self.assertRaises(ValueError): transition('COBRADO','PENDIENTE')
    def test_pending_paid(self): self.assertEqual(transition('PENDIENTE','COBRADO'),'COBRADO')
    def test_replay(self):
        self.assertEqual(replay(2000,{'monto_centavos':2000,'response':{'recarga_id':'r1'}}),{'recarga_id':'r1'})
    def test_conflict(self):
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            replay(3000,{'monto_centavos':2000,'response':{}})
    def test_event_stability(self): self.assertEqual(self.event()['detail']['evento_id'],self.event()['detail']['evento_id'])
    def test_wrong_group(self):
        e=self.event(); e['detail']['grupo']='other'
        with self.assertRaises(ValueError): validate_event(e)
    def test_timestamp_requires_timezone(self):
        with self.assertRaises(ValueError): paso_id('P1','T1','2026-11-06T12:00:00')
