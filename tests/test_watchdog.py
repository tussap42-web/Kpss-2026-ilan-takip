import copy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock

import watchdog

CFG = json.loads((watchdog.BASE / 'config.json').read_text())
NOW = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)


def state(age, status='partial_source_error'):
    return {'last_run': {'at': (NOW - timedelta(minutes=age)).isoformat(), 'status': status}}


class WatchdogTests(unittest.TestCase):
    def test_partial_source_outage_does_not_trigger_repair_loop(self):
        api = Mock(return_value={'workflow_runs': []})
        self.assertEqual(watchdog.check(CFG, state(30), NOW, api)['status'], 'healthy')
        self.assertEqual(api.call_count, 1)

    def test_stale_heartbeat_dispatches_real_scan_once(self):
        api = Mock(side_effect=[{'workflow_runs': []}, {}])
        self.assertEqual(watchdog.check(CFG, state(120), NOW, api)['status'], 'dispatch_scan')
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args.kwargs['payload'], {'ref': 'main', 'inputs': {'mode': 'scan', 'automatic_recovery': 'true'}})

    def test_active_scan_prevents_duplicate_recovery(self):
        runs = [{'status': 'in_progress', 'created_at': (NOW - timedelta(minutes=5)).isoformat()}]
        self.assertEqual(watchdog.decide(CFG, state(120), runs, NOW), 'scan_already_running')

    def test_recent_dispatch_has_cooldown_even_if_failed(self):
        runs = [{'status': 'completed', 'event': 'workflow_dispatch',
                 'created_at': (NOW - timedelta(minutes=5)).isoformat()}]
        self.assertEqual(watchdog.decide(CFG, state(120), runs, NOW), 'recovery_cooldown')

    def test_telegram_failure_retries_without_waiting_90_minutes(self):
        self.assertEqual(watchdog.decide(CFG, state(30, 'telegram_error'), [], NOW), 'dispatch_scan')

    def test_night_and_disabled_notifications_do_not_restart(self):
        night = NOW.replace(hour=23)
        self.assertEqual(watchdog.decide(CFG, state(120), [], night), 'quiet_hours')
        cfg = copy.deepcopy(CFG)
        cfg['notifications']['enabled'] = False
        self.assertEqual(watchdog.decide(cfg, state(120), [], NOW), 'disabled')

    def test_missing_invalid_or_future_heartbeat_recovers(self):
        for data in [{}, {'last_run': {'at': 'bad'}}, state(-120), state(0, 'telegram_error')]:
            self.assertEqual(watchdog.decide(CFG, data, [], NOW), 'dispatch_scan')

    def test_deployment_verification_exercises_dispatch(self):
        api = Mock(side_effect=[{'workflow_runs': []}, {}])
        self.assertEqual(watchdog.check(CFG, state(1), NOW, api, verify=True)['status'], 'dispatch_scan')

    def test_heartbeat_with_naive_time_is_invalid(self):
        self.assertIsNone(watchdog.parse_time('2026-11-09T14:30:00'))
