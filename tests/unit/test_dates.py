"""Pure date boundary checks for Guatemala accounting days."""
import unittest
from datetime import datetime, timezone

try:
    from reportes.recaudacion import utc_bounds, local_date
except ModuleNotFoundError as exc:
    if exc.name != 'boto3':
        raise
    # The deployed runtime and CloudShell include boto3. Keep these pure checks
    # runnable even in a minimal local Python environment.
    import sys
    from unittest.mock import MagicMock
    sys.modules['boto3'] = MagicMock()
    from reportes.recaudacion import utc_bounds, local_date
    sys.modules.pop('boto3', None)


class GuatemalaDates(unittest.TestCase):
    def test_midnight_local_and_exclusive_end(self):
        start, end = utc_bounds('2026-11-12')
        self.assertEqual(start.isoformat(), '2026-11-12T06:00:00+00:00')
        self.assertEqual(end.isoformat(), '2026-11-13T06:00:00+00:00')
        self.assertLess(start, datetime(2026, 11, 13, 5, 59, 59, tzinfo=timezone.utc))
        self.assertTrue(start <= datetime(2026, 11, 13, 5, 59, 59, tzinfo=timezone.utc) < end)
        self.assertFalse(start <= end < end)

    def test_month_year_and_leap_day(self):
        start, end = utc_bounds('2024-02-29')
        self.assertEqual(start.isoformat(), '2024-02-29T06:00:00+00:00')
        self.assertEqual(end.isoformat(), '2024-03-01T06:00:00+00:00')

    def test_noncanonical_or_invalid_date_is_rejected(self):
        for invalid in ('2026-02-30', '20261112', '2026-11-12T00:00:00', '', None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'INVALID_DATE'):
                local_date(invalid)


if __name__ == '__main__':
    unittest.main()
