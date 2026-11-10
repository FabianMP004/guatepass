import unittest
from copy import deepcopy
from common.models import make_event, validate_event, stable_id
from common.catalogo import SOURCES

def event(kind='saldo.recargado', payload=None):
    return make_event(kind,'r1',1,payload or {'recarga_id':'r1','placa':'P-456DEF','monto_centavos':2000},
                      'req','2026-11-09T12:00:00Z','20230223')

class CatalogueTests(unittest.TestCase):
    def test_canonical_source_and_schema(self):
        result=event()
        self.assertEqual(result['source'],'guatepass.saldo')
        self.assertEqual(result['detail']['schema_version'],2)
        self.assertEqual(result['detail']['evento_id'],stable_id('evento','saldo.recargado','r1',1))

    def test_wrong_source_rejected(self):
        result=event();result['source']='guatepass.pasos'
        with self.assertRaises(ValueError):validate_event(result)

    def test_old_envelope_preserved(self):
        old=event('RecargaAplicada');original=deepcopy(old)
        self.assertEqual(validate_event(old),original)
        self.assertEqual(old['source'],'guatepass')
        self.assertEqual(old['detail']['schema_version'],1)

    def test_invalid_money(self):
        for amount in (True,0,-1,2.5,'2000',None):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                event(payload={'recarga_id':'r1','placa':'P-456DEF','monto_centavos':amount})

    def test_missing_snapshot(self):
        with self.assertRaises(ValueError):event(payload={'placa':'P-456DEF'})

    def test_reject_version_mix(self):
        result=event();result['detail']['schema_version']=1
        with self.assertRaises(ValueError):validate_event(result)

    def test_phone_only_invitation(self):
        result=event('invitacion.solicitada',{'paso_id':'p1','placa':'P-456DEF','contacto':{'telefono':'50212345678'}})
        self.assertEqual(result['source'],'guatepass.registro')

    def test_invitation_needs_contact(self):
        with self.assertRaises(ValueError):event('invitacion.solicitada',{'paso_id':'p1','placa':'P-456DEF'})

    def test_catalogue_has_twelve_events(self):
        self.assertEqual(len(SOURCES),12)
