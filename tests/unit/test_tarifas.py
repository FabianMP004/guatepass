import unittest
from common.money import calcular_tarifa


class TarifasTests(unittest.TestCase):
    def test_zona10(self):
        self.assertEqual(calcular_tarifa(500, 'TAG'), 450)
        self.assertEqual(calcular_tarifa(500, 'APP'), 500)
        self.assertEqual(calcular_tarifa(500, 'NO_REGISTRADO'), 750)

    def test_half_up_centavo(self):
        self.assertEqual(calcular_tarifa(105, 'TAG'), 95)
        self.assertEqual(calcular_tarifa(101, 'NO_REGISTRADO'), 152)
        self.assertEqual(calcular_tarifa(1, 'TAG'), 1)

    def test_invalid(self):
        for value in (True, False, 0, -1, 500.0, '500', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calcular_tarifa(value, 'APP')
        with self.assertRaises(ValueError):
            calcular_tarifa(500, 'tag')
