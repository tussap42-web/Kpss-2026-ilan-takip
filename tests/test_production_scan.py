import copy
from datetime import date, datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import main
from eligibility import assess_notice
from notification_policy import notification_matches

CFG = json.loads((main.BASE / 'config.json').read_text())
TODAY = date(2026, 11, 19)
ITEM = {'title': 'Tokat memur alımı', 'url': 'https://example.bel.tr/ilan', 'source': 'Belediye'}


class ProductionScanTests(unittest.TestCase):
    def test_combined_kpss_column_and_special_condition(self):
        rows = [['Unvan', 'KPSS Puanı', 'Mezuniyet', 'Özel Şart'],
                ['Memur', 'P93, en az 60 puan', 'Herhangi bir önlisans programı',
                 '(B) sınıfı sürücü belgesine sahip olmak']]
        assessment = assess_notice('Başvurular 16/12/2026-18/12/2026 tarihleri arasında.',
                                   CFG, TODAY, rows=rows)
        self.assertEqual(assessment['application_start'], '2026-12-16')
        self.assertEqual(assessment['deadline'], '2026-12-18')
        item = dict(ITEM, assessment=assessment)
        self.assertEqual(len(notification_matches(item, CFG)), 1)
        self.assertIn('İNCELEME GEREKİYOR', main.message_for(item, CFG))
        self.assertIn('sürücü belgesi', main.message_for(item, CFG))

    def test_inclusive_school_requirement_respects_score_type(self):
        for score_type, expected in [('P93', []), ('P94', None)]:
            text = f'{score_type} en az 60 puan. En az lise mezunu olmak.'
            self.assertEqual(notification_matches(
                dict(ITEM, assessment=assess_notice(text, CFG, TODAY)), CFG), expected)

    def test_production_requires_open_application_and_official_link(self):
        score_text = 'P93 en az 65 puan. Herhangi bir önlisans programından mezun olmak.'
        cases = [
            (' Başvurular 16/12/2026-18/12/2026 tarihleri arasında.', ITEM['url'], 0),
            ('', ITEM['url'], 0),
            (' Son başvuru: 18.12.2026', 'https://example.org/job', 0),
            (' Son başvuru: 18.12.2026', ITEM['url'], 1),
        ]
        for dates, url, expected in cases:
            with self.subTest(dates=dates, url=url):
                with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,
                     TELEGRAM_BOT_TOKEN='fake', TELEGRAM_CHAT_IDS='1', FORCE_RUN='1'), \
                     patch.object(main, 'datetime') as clock, \
                     patch.object(main, 'scan_source', return_value=[dict(ITEM, url=url)]), \
                     patch.object(main, 'enrich', side_effect=lambda item: dict(item, text=score_text + dates)), \
                     patch.object(main, 'telegram_request') as sender:
                    clock.now.return_value = datetime(2026, 11, 19, 12)
                    main.scan(copy.deepcopy(CFG), {'sources': [dict(ITEM, name='Belediye')]}, Path(tmp) / 'seen.json', False)
                    self.assertEqual(sender.call_count, expected)

    def test_telegram_receipt_and_sanitized_connection_error(self):
        for payload in ({'ok': True, 'result': {'message_id': 12}}, {'ok': True}):
            with patch.object(main, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                if 'result' in payload:
                    self.assertEqual(main.telegram_request('fake', 'sendMessage', {}), payload)
                else:
                    with self.assertRaisesRegex(main.TelegramError, '^missing_message_receipt$'):
                        main.telegram_request('fake', 'sendMessage', {})
        with patch.object(main, 'urlopen', side_effect=URLError(TimeoutError('secret'))):
            with self.assertRaisesRegex(main.TelegramError, '^transport_TimeoutError$'):
                main.telegram_request('fake', 'sendMessage', {})
