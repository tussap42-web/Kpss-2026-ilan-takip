import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import main

CFG = json.loads((main.BASE/'config.json').read_text())
SOURCES = {'sources':[{'name':'Test','url':'https://example.org'}]}
ITEM = {'title':'Konya sözleşmeli personel alımı','source':'Test','url':'https://example.org/job'}


class DeliveryTests(unittest.TestCase):
    def run_scan(self, path, config=None, text='', send=None):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='fake', TELEGRAM_CHAT_IDS='1,2', FORCE_RUN='1'), patch.object(main,'scan_source', return_value=[ITEM]), patch.object(main,'enrich', side_effect=lambda i:dict(i,text=text)), patch.object(main,'telegram_request', side_effect=send) as mock:
            result=main.scan(config or CFG,SOURCES,path,False)
            return result,mock.call_count

    def test_high_score_never_sent(self):
        with tempfile.TemporaryDirectory() as d:
            result,count=self.run_scan(Path(d)/'state.json', text='P93 en az 75 puan')
            self.assertEqual((result,count),(0,0))

    def test_recipient_failure_does_not_block_others_and_retries(self):
        def send(token,method,payload):
            if payload['chat_id']=='1': raise RuntimeError('failure')
            return {'ok':True}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.json'
            result,count=self.run_scan(path,send=send)
            self.assertEqual((result,count),(1,2))
            result,count=self.run_scan(path)
            self.assertEqual((result,count),(0,1))

    def test_reminders_once_per_recipient(self):
        today=main.datetime.now(main.ZoneInfo('Europe/Istanbul')).date()
        text='Son başvuru: '+today.strftime('%d.%m.%Y')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.json'
            self.assertEqual(self.run_scan(path,text=text)[1],2)
            self.assertEqual(self.run_scan(path,text=text)[1],2)
            self.assertEqual(self.run_scan(path,text=text)[1],0)
            self.assertNotIn('fake',path.read_text())

    def test_disabled_new_notifications(self):
        config=copy.deepcopy(CFG)
        config['notifications']['notify_new_jobs']=False
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_scan(Path(d)/'s.json',config=config)[1],0)

    def test_incomplete_pdf_cannot_exclude(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, TELEGRAM_BOT_TOKEN='fake', TELEGRAM_CHAT_IDS='1', FORCE_RUN='1'), patch.object(main,'scan_source',return_value=[ITEM]), patch.object(main,'enrich',return_value=dict(ITEM,text='P3 en az 90 puan',detail_warning='PDF okunamadı')), patch.object(main,'telegram_request') as send:
            main.scan(CFG,SOURCES,Path(d)/'s.json',False)
            self.assertEqual(send.call_count,1)

    def test_preview_does_not_write_or_send(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main,'scan_source',return_value=[ITEM]), patch.object(main,'enrich',return_value=dict(ITEM,text='P93 en az 70 puan')), patch.object(main,'telegram_request') as send:
            path=Path(d)/'s.json'
            self.assertEqual(main.scan(CFG,SOURCES,path,True),0)
            self.assertFalse(path.exists())
            send.assert_not_called()

    def test_expired_notice_never_sent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_scan(Path(d)/'s.json',text='Son başvuru: 01.01.2020')[1],0)
