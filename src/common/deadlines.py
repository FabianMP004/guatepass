from datetime import datetime, timedelta, timezone

def deadline(ssm_client, parameter_name, now=None):
    """Call ONLY on first creation, persist result atomically with pending/dispute.
    Retries read persisted vence_en. Step Functions must use TimestampPath,
    not recalculate with the then-current SSM value. No SSM value cache.
    """
    seconds=int(ssm_client.get_parameter(Name=parameter_name)['Parameter']['Value'])
    if not 1 <= seconds <= 604800: raise ValueError('INVALID_TIMEOUT')
    start=now or datetime.now(timezone.utc)
    if start.tzinfo is None: raise ValueError('TIMEZONE_REQUIRED')
    return (start+timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
