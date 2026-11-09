"""Recover stale KPSS scans without touching Telegram secrets or delivery receipts."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
ACTIVE = {'queued', 'in_progress', 'waiting', 'pending', 'requested'}


def parse_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def decide(config, state, runs, now, verify=False):
    if not config['notifications'].get('enabled', False):
        return 'disabled'
    schedule = config['schedule']
    hour = now.astimezone(ZoneInfo(schedule['timezone'])).hour
    start, end = int(schedule['start_hour']), int(schedule['end_hour'])
    in_window = start <= hour <= end if start <= end else hour >= start or hour <= end
    if not in_window:
        return 'quiet_hours'
    for run in runs:
        stamp = parse_time(run.get('created_at'))
        if stamp is None:
            continue
        age = (now - stamp).total_seconds()
        if run.get('status') in ACTIVE and -300 <= age < 20 * 60 and not verify:
            return 'scan_already_running'
        if run.get('event') == 'workflow_dispatch' and -300 <= age < 15 * 60:
            return 'recovery_cooldown'
    last = state.get('last_run', {})
    stamp = parse_time(last.get('at'))
    healthy = last.get('status') in {'success', 'partial_source_error', 'delivery_deferred'}
    if not verify and stamp and healthy and -300 <= (now - stamp).total_seconds() < 90 * 60:
        return 'healthy'
    return 'dispatch_scan'


def github_request(path, method='GET', payload=None):
    token, repo = os.environ['GITHUB_TOKEN'], os.environ['GITHUB_REPOSITORY']
    request = Request(f'https://api.github.com/repos/{repo}/{path}',
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                               'X-GitHub-Api-Version': '2022-11-28'}, method=method)
    with urlopen(request, timeout=20) as response:
        body = response.read(2_000_000)
        return json.loads(body) if body else {}


def check(config, state, now, api=github_request, verify=False):
    runs = api('actions/workflows/kpss.yml/runs?per_page=30').get('workflow_runs', [])
    action = decide(config, state, runs, now, verify)
    if action == 'dispatch_scan':
        api('actions/workflows/kpss.yml/dispatches', method='POST',
            payload={'ref': 'main', 'inputs': {'mode': 'scan', 'automatic_recovery': 'true'}})
    return {'status': action, 'at': now.isoformat(), 'stale_after_minutes': 90,
            'last_scan_at': state.get('last_run', {}).get('at')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-recovery', action='store_true',
                        help='One deployment verification; queue a real scan during daytime.')
    args = parser.parse_args()
    config = json.loads((BASE / 'config.json').read_text())
    state_file = BASE / 'state/seen.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    if not isinstance(state, dict) or not isinstance(state.get('sent', {}), dict):
        raise ValueError('invalid_state')
    result = check(config, state, datetime.now(timezone.utc), verify=args.verify_recovery)
    print('WATCHDOG_SUMMARY ' + json.dumps(result))
    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as handle:
            handle.write('## KPSS otomatik toparlanma kontrolü\n\n```json\n' + json.dumps(result, indent=2) + '\n```\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        # Never log the token, request headers or unfiltered exception text.
        print('WATCHDOG_ERROR ' + json.dumps({'type': type(exc).__name__,
              'http_status': exc.code if isinstance(exc, HTTPError) else None}))
        raise SystemExit(1)
