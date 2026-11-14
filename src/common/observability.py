"""Correlated structured events, with an explicit field allowlist."""
import json
import logging

FIELDS = ('paso_id', 'evento_id', 'request_id', 'estado', 'error')
LOGGER = logging.getLogger('guatepass')
LOGGER.setLevel(logging.INFO)


def emit(**values):
    record = {key: str(values[key])[:180] for key in FIELDS if values.get(key) is not None}
    LOGGER.info(json.dumps(record, ensure_ascii=False, separators=(',', ':')))
