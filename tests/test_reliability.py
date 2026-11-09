import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import main
import network
from discovery import scan_source

CFG = json.loads((main.BASE / 'config.json').read_text())
ITEM = {'title': 'Konya personel alımı ilanı', 'source': 'Test', 'url': 'https://example.org/job'}
SOURCES = {'sources': [{'name': 'Test', 'url': 'https://example.org/'}]}


class ReliabilityTests(unittest.TestCase):
    def test_delayed_schedule_keeps_notice_and_delivers_next_day_once(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, TELEGRAM_BOT_TOKEN='fake',
                TELEGRAM_CHAT_IDS='1', FORCE_RUN='0', KPSS_TRIGGER='schedule', KPSS_STRICT_SOURCES='1'), \
                patch.object(main, 'datetime') as clock, patch.object(main, 'scan_source') as reader, \
                patch.object(main, 'enrich', side_effect=lambda i: dict(i, text='P93 en az 70 puan')), \
                patch.object(main, 'telegram_request') as sender, patch.object(main, 'report') as report:
            path = Path(d) / 'state.json'
            reader.return_value = [copy.deepcopy(ITEM)]
            clock.now.return_value = datetime(2026, 11, 9, 1, 47, tzinfo=main.ZoneInfo('Europe/Istanbul'))
            self.assertEqual(main.scan(CFG, SOURCES, path, False), 0)
            self.assertIn(ITEM['url'], json.loads(path.read_text())['notices'])
            self.assertFalse(json.loads(path.read_text())['sent'])
            self.assertEqual(report.call_args.args[0], 'delivery_deferred')
            sender.assert_not_called()
            # The notice may disappear from the website before the morning run.
            reader.return_value = []
            clock.now.return_value = datetime(2026, 11, 9, 9, 47, tzinfo=main.ZoneInfo('Europe/Istanbul'))
            self.assertEqual(main.scan(CFG, SOURCES, path, False), 0)
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(main.scan(CFG, SOURCES, path, False), 0)
            self.assertEqual(sender.call_count, 1)

    def test_partial_sources_fail_workflow_after_saving_successful_deliveries(self):
        sources = {'sources': [{'name': 'Test', 'url': 'https://example.org/'},
                               {'name': 'Offline', 'url': 'https://offline.example/'}]}
        def read(source):
            if source['name'] == 'Offline':
                raise OSError('unavailable')
            return [copy.deepcopy(ITEM)]
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, TELEGRAM_BOT_TOKEN='fake',
                TELEGRAM_CHAT_IDS='1', FORCE_RUN='1', KPSS_STRICT_SOURCES='1'), \
                patch.object(main, 'scan_source', side_effect=read), \
                patch.object(main, 'enrich', side_effect=lambda i: dict(i, text='P93 en az 70 puan')), \
                patch.object(main, 'telegram_request') as sender:
            path = Path(d) / 'state.json'
            self.assertEqual(main.scan(CFG, sources, path, False), 2)
            self.assertEqual(sender.call_count, 1)
            self.assertTrue(json.loads(path.read_text())['sent'])
            self.assertEqual(main.scan(CFG, sources, path, False), 2)
            self.assertEqual(sender.call_count, 1)

    def test_transient_get_retries_but_forbidden_and_long_backoff_do_not(self):
        headers = Message()
        error = HTTPError('https://example.org/', 503, 'Unavailable', headers, None)
        with patch('network._raw', side_effect=[error, (b'ok', headers, 'https://example.org/')]) as raw, \
                patch('network.time.sleep'):
            self.assertEqual(network._get_with_retry('https://example.org/', 100)[0], b'ok')
            self.assertEqual(raw.call_count, 2)
        for code, backoff in [(403, ''), (429, '120')]:
            headers = Message()
            headers['Retry-After'] = backoff
            error = HTTPError('https://example.org/', code, 'Error', headers, None)
            with patch('network._raw', side_effect=error) as raw, patch('network.time.sleep') as sleep:
                with self.assertRaises(HTTPError):
                    network._get_with_retry('https://example.org/', 100)
                self.assertEqual(raw.call_count, 1)
                sleep.assert_not_called()

    def test_robots_failure_is_reported_separately_from_explicit_disallow(self):
        for body, error, expected in [(b'User-agent: *\nDisallow: /', None, 'robots_disallowed'),
                (None, HTTPError('https://robots.example/robots.txt', 403, 'Forbidden', Message(), None), 'robots_http_error')]:
            network._policies.clear()
            with patch('network._raw', return_value=(body, Message(), 'https://robots.example/robots.txt'), side_effect=error):
                with self.assertRaises(network.SourceAccessError) as caught:
                    network.fetch_bytes('https://robots.example/job')
                self.assertEqual(network.error_info(caught.exception)['reason'], expected)

    def test_redirected_html_robots_is_read_as_prefix_not_a_large_policy(self):
        class Response(io.BytesIO):
            headers = Message()
            headers['Content-Type'] = 'text/html'
            def geturl(self):
                return 'https://redirected.example/Home/Index'
        robot_headers = Message()
        robot_headers['Location'] = '/Home/Index'
        redirect = HTTPError('https://redirected.example/robots.txt', 302, 'Found', robot_headers, None)
        network._policies.clear()
        with patch('network._raw', side_effect=[redirect, (b'', Message(), 'https://redirected.example/Home/Index'),
                (b'<html>listing</html>', Message(), 'https://redirected.example/')]) as raw, patch('network.time.sleep'):
            self.assertEqual(network.fetch_bytes('https://redirected.example/')[0], b'<html>listing</html>')
            self.assertTrue(raw.call_args_list[1].kwargs['robots'])
        response = Response(b'<!doctype html><html>' + b'x' * 600000)
        with patch('network.build_opener') as opener:
            opener.return_value.open.return_value = response
            body, _, _ = network._raw('https://redirected.example/Home/Index', 512000, robots=True)
            self.assertEqual(body, b'')

    def test_source_diagnostics_keep_http_status_without_query_or_exception_text(self):
        source = {'name': 'Test', 'url': 'https://example.org/?secret=hidden'}
        error = HTTPError(source['url'], 503, 'token=hidden', Message(), None)
        with self.assertRaises(OSError):
            scan_source(source, lambda url: (_ for _ in ()).throw(error), main.is_job_link, main.normalize)
        details = source['_scan_stats']['error_details']
        self.assertEqual(details[0]['http_status'], 503)
        self.assertNotIn('hidden', json.dumps(details))
