import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import main
import network
from dates_tr import deadlines
from discovery import scan_source, canonical
from eligibility import assess_notice, norm, table_positions
from details import enrich, DocumentText

CFG = json.loads((main.BASE/'config.json').read_text())
TODAY = date(2026,11,8)
ITEM = {'title':'Konya personel alımı ilanı','source':'Test','url':'https://example.org/job'}
SOURCES = {'sources':[{'name':'Test','url':'https://example.org'}]}


class AuditTests(unittest.TestCase):
    def test_deadline_formats(self):
        for text in ['Son başvuru: 15.11.2026', 'Başvuru bitiş tarihi: 15/11/2026',
                     'Başvurular 10-15 Kasım 2026 tarihleri arasında alınacaktır.',
                     "Son müracaat tarihi 15 Kasım 2026’dır.",
                     'Başvurular 10.11.2026-15.11.2026 tarihleri arasında']:
            self.assertEqual(deadlines(norm(text)),{date(2026,11,15)},text)

    def test_date_ambiguity_and_invalid_ranges(self):
        self.assertEqual(len(deadlines(norm('Son başvuru: 15 Kasım 2026. Son müracaat tarihi 16 Kasım 2026'))),2)
        self.assertFalse(deadlines(norm('Başvurular 15-10 Kasım 2026 tarihleri arasında')))
        self.assertFalse(deadlines(norm('Son başvuru: 31 Şubat 2026')))
        self.assertFalse(deadlines(norm('Yayın tarihi 15 Kasım 2026')))

    def test_positions_separate_scores(self):
        a=assess_notice('Büro personeli: P93 en az 75\nDestek personeli: P93 en az 60',CFG,TODAY)
        self.assertEqual(a['status'],'review')
        self.assertEqual(len(a['positions']),2)
        self.assertEqual([r['assessment']['status'] for r in a['positions']],['excluded','review'])
        self.assertEqual([r['assessment']['minimum_score'] for r in a['positions']],[75,60])

    def test_labeled_table_columns(self):
        rows=[['Unvan','KPSS Puan Türü','Taban Puan','Mezuniyet / Nitelik'],
              ['Büro personeli','P93','75','Ön lisans 3001'],
              ['Destek personeli','P93','60','Grafik Tasarım 3001']]
        a=assess_notice('İlan metni',CFG,TODAY,rows=rows)
        self.assertEqual(len(a['positions']),2)
        self.assertEqual(a['positions'][1]['qualification_codes'],['3001'])
        self.assertIn('grafik tasarim',a['positions'][1]['education'])
        self.assertEqual(a['positions'][0]['assessment']['minimum_score'],75)
        self.assertEqual(a['positions'][1]['assessment']['minimum_score'],60)

    def test_unlabeled_pdf_table_never_guessed(self):
        rows=[['Büro','P93','10','75'],['Destek','P93','30','60']]
        self.assertFalse(table_positions(rows))

    def test_mixed_flat_notice_not_excluded(self):
        a=assess_notice('Büro personeli P93 en az 75 puan; destek personeli şartları ektedir.',CFG,TODAY)
        self.assertEqual(a['status'],'review')

    def test_local_dates_not_global(self):
        a=assess_notice('Büro personeli P93 en az 75 puan Son başvuru: 15.11.2026\nDestek personeli P93 en az 60 puan Son başvuru: 20.11.2026',CFG,TODAY)
        self.assertIsNone(a['deadline'])
        self.assertEqual(a['positions'][0]['assessment']['deadline'],'2026-11-15')
        self.assertEqual(a['positions'][1]['assessment']['deadline'],'2026-11-20')

    def test_partial_document_cannot_exclude(self):
        self.assertEqual(assess_notice('P93 en az 99 puan',CFG,TODAY,incomplete=True)['status'],'review')

    def test_navigation_and_pagination(self):
        pages={'https://example.org/':'<a href="/duyurular">Duyurular</a>',
               'https://example.org/duyurular':'<a href="/job">Konya personel alımı ilanı</a><a rel="next" href="/duyurular?page=2">2</a>',
               'https://example.org/duyurular?page=2':'<a href="/job2">Memur alımı başvuruları</a><a href="https://outside.org/job">Personel alımı ilanı</a>'}
        source={'url':'https://example.org/','name':'Test','max_pages':3}
        found=scan_source(source,pages.__getitem__,main.is_job_link,main.normalize)
        self.assertEqual(len(found),2)
        self.assertEqual(source['_scan_stats']['pages_ok'],3)

    def test_feed(self):
        pages={'https://example.org/':'<link rel="alternate" type="application/rss+xml" href="/rss">',
               'https://example.org/rss':'<rss><channel><item><title>Personel alımı ilanı</title><link>https://example.org/job</link></item></channel></rss>'}
        source={'url':'https://example.org/','name':'Test','max_pages':2}
        self.assertEqual(len(scan_source(source,pages.__getitem__,main.is_job_link,main.normalize)),1)

    def test_partial_navigation_failure_reported(self):
        def read(url):
            if 'duyurular' in url: raise OSError('test')
            return '<a href="/duyurular">Duyurular</a>'
        source={'url':'https://example.org/','name':'Test'}
        scan_source(source,read,main.is_job_link,main.normalize)
        self.assertEqual(source['_scan_stats']['pages_failed'],1)

    def test_url_normalization(self):
        self.assertEqual(canonical('https://example.org/job?b=2&a=1&utm_source=x#abc'),canonical('https://example.org/job?a=1&b=2'))

    def scan(self,path,cfg=None,items=None,text='',document_key=None,send=None):
        def rich(item): return dict(item,text=text,**({'document_key':document_key} if document_key else {}))
        with patch.dict(os.environ,TELEGRAM_BOT_TOKEN='fake',TELEGRAM_CHAT_IDS='1,2',FORCE_RUN='1'), patch.object(main,'scan_source',return_value=items if items is not None else [ITEM]), patch.object(main,'enrich',side_effect=rich), patch.object(main,'telegram_request',side_effect=send) as sender:
            code=main.scan(cfg or CFG,SOURCES,path,False)
        return code,sender.call_args_list

    def test_total_message_cap_and_resume(self):
        cfg=copy.deepcopy(CFG);cfg['notifications']['max_notifications_per_run']=1
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'seen.json'
            self.assertEqual(len(self.scan(path,cfg)[1]),1)
            self.assertEqual(len(self.scan(path,cfg)[1]),1)
            self.assertEqual(len(self.scan(path,cfg)[1]),0)

    def test_failed_first_recipient_cannot_starve_second_with_cap_one(self):
        cfg=copy.deepcopy(CFG);cfg['notifications']['max_notifications_per_run']=1
        def send(t,m,p):
            if p['chat_id']=='1': raise RuntimeError('test')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            self.scan(path,cfg,send=send)
            calls=self.scan(path,cfg,send=send)[1]
            self.assertEqual(calls[0][0][2]['chat_id'],'2')

    def test_cross_source_exact_document_dedup(self):
        other=dict(ITEM,url='https://example.org/job2',source='Other')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            self.assertEqual(len(self.scan(path,items=[ITEM,other],document_key='identical')[1]),2)
            self.assertEqual(len(self.scan(path,items=[other],document_key='identical')[1]),0)

    def test_preserve_legacy_receipts_and_resume_missing_recipient(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            state={'sent':{ITEM['url']:{'title':'old','chats':[main.chat_fingerprint('fake','1')],'at':'old'}}}
            path.write_text(json.dumps(state))
            calls=self.scan(path)[1]
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][0][2]['chat_id'],'2')
            self.assertIn(main.chat_fingerprint('fake','1'),json.loads(path.read_text())['sent'][ITEM['url']]['chats'])

    def test_disappeared_backlog_is_not_lost(self):
        cfg=copy.deepcopy(CFG);cfg['notifications']['max_notifications_per_run']=1
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json';self.scan(path,cfg)
            self.assertEqual(len(self.scan(path,cfg,items=[])[1]),1)

    def test_corrupt_state_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json';path.write_text('broken')
            with self.assertRaises(ValueError):self.scan(path)
            self.assertEqual(path.read_text(),'broken')

    def test_recovered_detail_clears_old_error(self):
        with patch('details.read_document',return_value=('P93 en az 70 puan',[])):
            item=enrich(dict(ITEM,detail_warning='old',document_key='stale'))
        self.assertNotIn('detail_warning',item)
        self.assertNotIn('document_key',item)

    def test_run_status_skip_and_source_failure(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,FORCE_RUN='0'), patch.object(main,'datetime') as clock, patch.object(main,'scan_source') as reader:
            clock.now.return_value=datetime(2026,11,8,1,39,tzinfo=main.ZoneInfo('Europe/Istanbul'))
            with patch.object(main,'report') as report:
                self.assertEqual(main.scan(CFG,SOURCES,Path(d)/'s.json',False),0)
                self.assertEqual(report.call_args[0][0],'skipped_time_window')
                reader.assert_not_called()
        with tempfile.TemporaryDirectory() as d, patch.object(main,'scan_source',side_effect=OSError),patch.object(main,'report') as report:
            self.assertEqual(main.scan(CFG,SOURCES,Path(d)/'s.json',True),1)
            self.assertEqual(report.call_args[0][0],'source_error')

    def test_robots_disallow_and_rate_limit(self):
        headers=Message();headers['Content-Type']='text/html'
        network._policies.clear();network._last.clear()
        with patch('network._raw',return_value=(b'User-agent: *\nDisallow: /private',headers,'https://robots.example/robots.txt')) as raw:
            with self.assertRaises(PermissionError):network.fetch_bytes('https://robots.example/private')
            self.assertEqual(raw.call_count,1)
        network._policies.clear();network._last.clear()
        with patch('network._raw',return_value=(b'User-agent: *\nCrawl-delay: 2',headers,'https://robots.example/')),patch('network.time.sleep') as sleep:
            network.fetch_bytes('https://robots.example/job')
            self.assertTrue(sleep.called)

    def test_real_pdf_table_layout_and_rows(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from details import read_document
        writer=PdfWriter();page=writer.add_blank_page(width=700,height=300)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Courier')})
        fonts=DictionaryObject({NameObject('/F1'):writer._add_object(font)})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):fonts})
        stream=DecodedStreamObject()
        stream.set_data(b'BT /F1 10 Tf 20 250 Td (Unvan               KPSS Puan Turu    Taban Puan    Mezuniyet) Tj 0 -20 Td (Buro personeli      P93               75            on lisans 3001) Tj 0 -20 Td (Destek personeli    P93               60            on lisans 3001) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        output=io.BytesIO();writer.write(output)
        headers=Message();headers['Content-Type']='application/pdf'
        with patch('details.fetch_bytes',return_value=(output.getvalue(),headers,'https://example.org/job.pdf')):
            text,_=read_document('https://example.org/job.pdf')
        import re
        rows=[re.split(r' {2,}',line.strip()) for line in text.splitlines() if re.search(r' {2,}',line.strip())]
        a=assess_notice(text,CFG,TODAY,rows=rows)
        self.assertEqual([row['assessment']['minimum_score'] for row in a['positions']],[75,60])

    def test_new_notice_precedes_daily_old_refresh(self):
        cfg=copy.deepcopy(CFG);cfg['processing']['max_details_per_run']=1
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            self.scan(path,cfg)
            state=json.loads(path.read_text());state['notices'][ITEM['url']]['checked_on']='2020-01-01'
            path.write_text(json.dumps(state))
            other=dict(ITEM,url='https://example.org/new')
            self.scan(path,cfg,items=[ITEM,other])
            self.assertIn(other['url'],json.loads(path.read_text())['notices'])

    def test_incomplete_attachments_clear_deadline(self):
        a=assess_notice('Son başvuru: 01.01.2020 P93 en az 99 puan',CFG,TODAY,incomplete=True)
        self.assertIsNone(a['deadline'])
        self.assertEqual(a['status'],'review')

    def test_ambiguous_table_cannot_trigger_whole_notice_rejection(self):
        rows=[['Kadro','KPSS','Sayı','Puan'],['Mühendis','P93','10','75'],['Başka','P93','20','60']]
        a=assess_notice('KPSS P93 en az 75 puan',CFG,TODAY,rows=rows)
        self.assertEqual(a['status'],'review')
