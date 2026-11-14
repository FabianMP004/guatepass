"""Camera load simulator. Manifests contain no API keys, credentials or tokens."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import boto3


TRANSIENT = {429, 500, 502, 503, 504}


def stamp(moment):
    return moment.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def make_notices(mode, count, duration, plates, toll, tag):
    if mode == 'duplicate':
        count = 3
    elif mode == 'single':
        count = 1
    elif mode == 'concurrent':
        count = max(2, count)
    elif count < 500 or duration < 60:
        raise ValueError('LOAD_REQUIRES_500_STEPS_OVER_60_SECONDS')
    start = datetime.now(timezone.utc)
    notices = []
    for index in range(count):
        moment = start + timedelta(microseconds=(index * duration * 1_000_000 // count)
                                   if mode == 'load' else index)
        notice = {'placa': plates[index % len(plates)], 'peaje_id': toll,
                  'timestamp': stamp(moment)}
        if tag:
            notice['tag_id'] = tag
        notices.append(notice)
    if mode == 'duplicate':
        notices = [dict(notices[0]) for _ in range(3)]
    return notices


def post(api_url, key, notice, attempts=4):
    results = []
    for index in range(attempts):
        begun = time.monotonic()
        request = Request(api_url.rstrip('/') + '/webhook/toll',
            data=json.dumps(notice, separators=(',', ':')).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'x-api-key': key})
        status = None
        try:
            with urlopen(request, timeout=15) as response:
                status = response.status
                body = json.load(response)
        except HTTPError as exc:
            status = exc.code
            try:
                body = json.load(exc)
            except (ValueError, UnicodeError):
                body = {}
        except (URLError, TimeoutError, OSError, ValueError):
            status = None
            body = {}
        latency = round((time.monotonic() - begun) * 1000, 2)
        result = {'attempt': index + 1, 'status': status,
                  'latency_ms': latency,
                  'request_id': body.get('request_id') if isinstance(body, dict) else None,
                  'paso_id': ((body.get('data') or {}).get('paso_id')
                              if isinstance(body, dict) else None),
                  'code': body.get('codigo') if isinstance(body, dict) else None}
        results.append(result)
        if status == 202:
            break
        if status not in TRANSIENT and status is not None:
            break
        if index + 1 < attempts:
            time.sleep(min(0.25 * (2 ** index), 2) + random.uniform(0, 0.1))
    return results


def percentiles(values):
    if not values:
        return {'max_ms': None, 'p95_ms': None, 'p99_ms': None, 'over_1000ms': 0}
    sorted_values = sorted(values)
    return {'max_ms': max(values),
            'p95_ms': sorted_values[max(0, (95 * len(values) + 99) // 100 - 1)],
            'p99_ms': sorted_values[max(0, (99 * len(values) + 99) // 100 - 1)],
            'over_1000ms': sum(value >= 1000 for value in values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='guatepass-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--mode', choices=['single', 'duplicate', 'concurrent', 'load'], required=True)
    parser.add_argument('--count', type=int, default=None)
    parser.add_argument('--duration', type=int, default=60)
    parser.add_argument('--workers', type=int, default=25)
    parser.add_argument('--plates', default='P-123ABC', help='Placas separadas por coma')
    parser.add_argument('--toll', default='PEAJE_ZONA10')
    parser.add_argument('--tag', default=None)
    parser.add_argument('--manifest-dir', default='runs')
    args = parser.parse_args()
    count = args.count if args.count is not None else (500 if args.mode == 'load' else 2)
    if not 1 <= args.workers <= 100 or count <= 0 or args.duration <= 0:
        parser.error('count, duration y workers deben ser positivos')
    plates = [plate.strip().upper() for plate in args.plates.split(',') if plate.strip()]
    if not plates:
        parser.error('Falta al menos una placa')
    notices = make_notices(args.mode, count, args.duration, plates, args.toll, args.tag)
    session = boto3.Session(region_name=args.region)
    outputs = {row['OutputKey']: row['OutputValue'] for row in session.client('cloudformation')
        .describe_stacks(StackName=args.stack)['Stacks'][0]['Outputs']}
    # Keep the camera key only in process memory, never in a manifest or output.
    key = session.client('apigateway').get_api_key(apiKey=outputs['WebhookApiKeyId'],
                                                 includeValue=True)['value']
    run_id = uuid4().hex
    path = Path(args.manifest_dir) / (run_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'schema': 1, 'run_id': run_id, 'stack': args.stack, 'region': args.region,
                'mode': args.mode, 'started_at': stamp(datetime.now(timezone.utc)),
                'duration_seconds': args.duration if args.mode == 'load' else None,
                'entries': [{'index': i, 'notice': notice, 'attempts': []}
                            for i, notice in enumerate(notices)]}
    path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    begun = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(args.workers, len(notices))) as pool:
        pending = {}
        for index, notice in enumerate(notices):
            if args.mode == 'load':
                due = begun + index * args.duration / len(notices)
                if due > time.monotonic():
                    time.sleep(due - time.monotonic())
            pending[pool.submit(post, outputs['ApiUrl'], key, notice)] = index
        for future in as_completed(pending):
            manifest['entries'][pending[future]]['attempts'] = future.result()
    manifest['ended_at'] = stamp(datetime.now(timezone.utc))
    path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    latencies = [attempt['latency_ms'] for row in manifest['entries']
                 for attempt in row['attempts']]
    successes = [row for row in manifest['entries']
                 if row['attempts'] and row['attempts'][-1]['status'] == 202]
    print(json.dumps({'run_id': run_id, 'manifest': str(path), 'avisos': len(notices),
        'pasos_unicos': len({(row['notice']['placa'], row['notice']['peaje_id'],
                            row['notice']['timestamp']) for row in manifest['entries']}),
        'respuestas_202': len(successes), 'fallos_definitivos': len(notices)-len(successes),
        'intentos': len(latencies), 'latencia_intentos': percentiles(latencies),
        'latencia_respuestas': percentiles([r['attempts'][-1]['latency_ms'] for r in successes]),
        'duracion_real_segundos': round(time.monotonic()-begun, 2)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
