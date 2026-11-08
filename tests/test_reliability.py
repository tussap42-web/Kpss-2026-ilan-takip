"""Regression cases from the audit; no live messages, accounts or private profiles."""
import copy
import io
import json
import os
from contextlib import redirect_stdout
from datetime import date, datetime
from email.message import Message
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import main
from dates_tr import application_period, deadlines
from details import read_document, enrich
from discovery import scan_source
from eligibility import assess_notice
from network import safe_error
from notice_types import notice_kind

CFG = json.loads((main.BASE/'config.example.json').read_text())
TODAY = date(2026,11,8)
NOW = datetime(2026,11,8,12,tzinfo=main.ZoneInfo('Europe/Istanbul'))
ITEM = {'title':'Büro personeli alım ilanı','url':'https://example.org/a','source':'Audit'}
SOURCES = {'sources':[{'name':'Audit','url':'https://example.org/'}]}


class ReliabilityTests(unittest.TestCase):
    def run_scan(self, path, items=None, text='P93 en az 60 puan', config=None, enrich_fn=None, source_error=None, now=NOW):
        output=io.StringIO()
        with patch.dict(os.environ,TELEGRAM_BOT_TOKEN='audit-fake',TELEGRAM_CHAT_IDS='1,2',FORCE_RUN='1'), \
             patch.object(main,'datetime') as clock, \
             patch.object(main,'scan_source',return_value=[ITEM] if items is None else items,side_effect=source_error), \
             patch.object(main,'enrich',side_effect=enrich_fn or (lambda i:dict(i,text=text))), \
             patch.object(main,'telegram_request',return_value={'ok':True}) as sender, \
             redirect_stdout(output):
            clock.now.return_value=now
            clock.fromisoformat.side_effect=datetime.fromisoformat
            code=main.scan(config or CFG,copy.deepcopy(SOURCES),path,False)
        return code,sender.call_count,json.loads(path.read_text()),output.getvalue()

    def test_non_recruitment_titles_and_truncated_result_path(self):
        cases=[('4924 sözleşmeli personel hakkında genelge','/genelge'),
               ('Personel alımı sonuçlarına ilişkin duyuru','/x'),
               ('Personel alımı yedek aday duyurusu','/x'),
               ('Destek personeli uygulamalı sınav yeri ve tarihi','/x'),
               ('KPSS-2018/1 yerleştirmesi...','/yerlestirmesi-sonucu-yerlestirilen-adaylar'),
               ('Başvuru Bilgileri','/ozel-istihdam-burolari/basvuru-bilgileri')]
        for title,path in cases:
            with self.subTest(title=title):
                self.assertFalse(main.is_job_link(title,'https://example.org'+path))

    def test_recruitment_and_exam_recruitment_still_discovered(self):
        for title in ['Konya Belediyesi İtfaiye Eri Alım İlanı','Zabıta Memuru Alım İlanı',
                      'Sözleşmeli bilişim personeli alım sınav ilanı','Büro personeli alımı ilanı']:
            with self.subTest(title=title):
                self.assertTrue(main.is_job_link(title,'https://example.org/job'))

    def test_body_mentions_of_future_results_do_not_reclassify_recruitment(self):
        self.assertEqual(notice_kind('Personel alımı ilanı','https://example.org/job'),'recruitment')
        # Only the actual h1/heading is used, not every occurrence of "sonuç".
        headers=Message();headers['Content-Type']='text/html'
        body=b'<h1>Personel alimi ilani</h1><p>Sonuclar daha sonra ilan edilir.</p>'
        with patch('details.fetch_bytes',return_value=(body,headers,ITEM['url'])):
            item=enrich(ITEM)
        self.assertEqual(notice_kind(item['title'],item['url'],item['document_title']),'recruitment')

    def test_full_heading_rejects_misleading_short_link(self):
        with tempfile.TemporaryDirectory() as d:
            code,count,_,_=self.run_scan(Path(d)/'s.json',enrich_fn=lambda i:dict(i,text='P93 en az 60 puan',document_title='Personel alımı sonuç duyurusu'))
        self.assertEqual((code,count),(0,0))

    def test_cached_circular_cannot_be_sent_on_upgrade(self):
        circular=dict(ITEM,title='Sözleşmeli personel alımı hakkında genelge')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            path.write_text(json.dumps({'sent':{},'notices':{circular['url']:dict(circular,assessment={'status':'review'},policy='old')}}))
            _,count,state,_=self.run_scan(path,items=[],text='P93 en az 60 puan')
        self.assertEqual(count,0)
        self.assertEqual(state['notices'][circular['url']]['notice_kind'],'regulation')

    def test_wrong_type_and_high_minimum_single_position_never_sent(self):
        for text in ['Büro personeli P3 en az 60 puan','Büro personeli P93 en az 75 puan',
                     'Büro personeli P94 en az 60 puan']:
            with self.subTest(text=text),tempfile.TemporaryDirectory() as d:
                _,count,state,_=self.run_scan(Path(d)/'s.json',text=text)
                a=state['notices'][ITEM['url']]['assessment']
                self.assertTrue(a['positions_complete'])
                self.assertEqual(a['status'],'excluded')
                self.assertEqual(count,0)

    def test_mixed_positions_keep_matching_position(self):
        a=assess_notice('Büro personeli P93 en az 75 puan\nDestek personeli P93 en az 60 puan',CFG,TODAY)
        self.assertEqual(a['status'],'review')
        self.assertEqual([p['assessment']['status'] for p in a['positions']],['excluded','review'])

    def test_all_explicit_positions_rejected_but_incomplete_coverage_is_not(self):
        text='Büro personeli P93 en az 75 puan\nDestek personeli P3 en az 60 puan'
        self.assertEqual(assess_notice(text,CFG,TODAY)['status'],'excluded')
        self.assertEqual(assess_notice(text+'\nDiğer kadrolar ektedir.',CFG,TODAY)['status'],'review')
        self.assertEqual(assess_notice(text,CFG,TODAY,incomplete=True)['status'],'review')

    def test_single_parsed_row_does_not_hide_unparsed_position(self):
        text='Büro personeli P93 en az 75 puan\nMühendis şartları ekteki tabloda yer alır.'
        a=assess_notice(text,CFG,TODAY)
        self.assertFalse(a['positions_complete'])
        self.assertEqual(a['status'],'review')

    def test_worded_score_table_and_partial_table(self):
        rows=[['Unvan','KPSS Puan Türü','Taban Puan','Mezuniyet'],
              ['Büro personeli','P93','En az 75 Puan','Ön lisans']]
        a=assess_notice('Kadro tablosu',CFG,TODAY,rows=rows)
        self.assertTrue(a['positions_complete'])
        self.assertEqual(a['status'],'excluded')
        rows.append(['Mühendis','P3','Ekte','Lisans'])
        a=assess_notice('Kadro tablosu',CFG,TODAY,rows=rows)
        self.assertFalse(a['positions_complete'])
        self.assertEqual(a['status'],'review')

    def test_sbb_explicit_closing_sentence(self):
        # Short factual excerpt; full source URL is documented in docs/reliability-phase-1.md.
        text='Başvurular 22.10.2026 tarihinde başlayacak ve 05.11.2026 tarihinde saat 18.00’da sona erecektir.'
        period=application_period(text)
        self.assertEqual(period['deadline'],date(2026,11,5))
        self.assertEqual(period['opens_on'],date(2026,10,22))
        self.assertEqual(period['deadline_time'],'18:00')
        self.assertEqual(assess_notice(text,CFG,TODAY)['status'],'excluded')

    def test_spaced_pdf_application_range(self):
        text='Yukarıda sayılan başvuru belgelerini 02/06/    2026-    04/06/2026 tarihleri arasında teslim ediniz.'
        self.assertEqual(deadlines(text),{date(2026,6,4)})
        self.assertEqual(assess_notice(text,CFG,TODAY)['status'],'excluded')

    def test_exam_or_publication_range_is_not_application_deadline(self):
        self.assertFalse(deadlines('Sınav 02/06/2026-04/06/2026 tarihleri arasında yapılacaktır.'))
        self.assertFalse(deadlines('Başvurular internetten yapılır. Sınav 02/06/2026-04/06/2026 tarihleri arasında yapılacaktır.'))
        self.assertFalse(deadlines('Yayımlanma tarihi: 05.11.2026'))

    def test_application_section_may_describe_exam_participation(self):
        text='Başvuru yeri, tarihi, şekli ve süresi: Adaylar, sözlü ve uygulamalı sınava katılabilmek için; 25/09/2023-29/09/2023 tarihleri arasında başvuracaklardır.'
        self.assertEqual(deadlines(text),{date(2023,9,29)})

    def test_verified_application_period_survives_unclear_qualification_table(self):
        text='Büro personeli ve destek personeli şartları tablodadır. Başvurular 02.06.2026-04.06.2026 tarihleri arasında alınır.'
        rows=[['Kadro','Sayı','Puan'],['Büro','10','60'],['Destek','20','70']]
        a=assess_notice(text,CFG,TODAY,rows=rows)
        self.assertEqual(a['deadline'],'2026-06-04')
        self.assertEqual(a['status'],'excluded')
        # Missing attachment can contain another application period: stay uncertain.
        a=assess_notice(text,CFG,TODAY,rows=rows,incomplete=True)
        self.assertIsNone(a['deadline'])
        self.assertEqual(a['status'],'review')

    def test_ambiguous_and_invalid_dates_are_not_guessed(self):
        self.assertIsNone(application_period('Son başvuru: 31.02.2026')['deadline'])
        self.assertIsNone(application_period('Son başvuru: 10.11.2026 Son başvuru: 11.11.2026')['deadline'])
        self.assertIsNone(application_period('Başvurular 4 Haziran Perşembe mesai bitimine kadar sürüyor.')['deadline'])

    def test_today_after_closing_time_is_not_notified(self):
        with tempfile.TemporaryDirectory() as d:
            _,count,_,_=self.run_scan(Path(d)/'s.json',text='Son başvuru: 08.11.2026 saat 10:00')
        self.assertEqual(count,0)

    def test_cached_notice_is_rechecked_at_send_time_after_same_day_cutoff(self):
        cfg=copy.deepcopy(CFG);cfg['notifications']['max_notifications_per_run']=1
        morning=NOW.replace(hour=9)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            _,count,_,_=self.run_scan(path,config=cfg,text='Son başvuru: 08.11.2026 saat 10:00',now=morning)
            self.assertEqual(count,1)
            # Its detail cache is from the same day and must not override the clock.
            _,count,_,_=self.run_scan(path,config=cfg,text='unused',now=NOW)
            self.assertEqual(count,0)

    def test_not_yet_open_is_not_notified(self):
        with tempfile.TemporaryDirectory() as d:
            _,count,_,_=self.run_scan(Path(d)/'s.json',text='Başvurular 10-15 Kasım 2026 tarihleri arasında alınacaktır.')
        self.assertEqual(count,0)

    def test_incomplete_requirements_retain_only_observed_date(self):
        a=assess_notice('Son başvuru: 05.11.2026',CFG,TODAY,incomplete=True)
        self.assertEqual(a['observed_deadline'],'2026-11-05')
        self.assertIsNone(a['deadline'])
        self.assertEqual(a['application_status'],'unverified')

    def test_undated_news_quarantined_without_guessing_year(self):
        news=dict(ITEM,url='https://example.org/haber/personel-alimi')
        with tempfile.TemporaryDirectory() as d:
            _,count,state,_=self.run_scan(Path(d)/'s.json',items=[news],text='Başvurular 4 Haziran Perşembe günü mesai bitimine kadar sürüyor.')
        self.assertEqual(count,0)
        self.assertTrue(state['notices'][news['url']]['requires_date_verification'])

    def test_discoveries_survive_detail_budget_and_disappearing_listing(self):
        cfg=copy.deepcopy(CFG);cfg['processing']['max_details_per_run']=1
        second=dict(ITEM,url='https://example.org/b')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            _,_,state,_=self.run_scan(path,items=[ITEM,second],config=cfg)
            self.assertEqual(len(state['notices']),2)
            self.assertEqual(state['notices'][second['url']]['detail_status'],'pending')
            _,count,state,_=self.run_scan(path,items=[],config=cfg)
            self.assertEqual(state['notices'][second['url']]['detail_status'],'checked')
            self.assertEqual(count,2)

    def test_discovery_is_saved_before_detail_process_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            with self.assertRaises(RuntimeError):
                self.run_scan(path,enrich_fn=lambda i:(_ for _ in ()).throw(RuntimeError('simulated crash')))
            self.assertEqual(json.loads(path.read_text())['notices'][ITEM['url']]['detail_status'],'pending')

    def test_partial_source_error_is_visible_but_other_source_delivers(self):
        sources={'sources':[{'name':'Good','url':'https://example.org/'},{'name':'Bad','url':'https://bad.example/'}]}
        def reader(s):
            if s['name']=='Bad':raise OSError('no private details')
            return [ITEM]
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ,TELEGRAM_BOT_TOKEN='fake',TELEGRAM_CHAT_IDS='1',FORCE_RUN='1'), \
             patch.object(main,'scan_source',side_effect=reader), \
             patch.object(main,'enrich',side_effect=lambda i:dict(i,text='P93 en az 60 puan')), \
             patch.object(main,'telegram_request') as sender,redirect_stdout(io.StringIO()):
            path=Path(d)/'s.json';code=main.scan(CFG,sources,path,False)
            self.assertEqual(code,1)
            self.assertEqual(sender.call_count,1)
            self.assertEqual(json.loads(path.read_text())['source_health']['Bad']['consecutive_failures'],1)

    def test_source_failure_count_and_last_success_survive(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            _,_,old,_=self.run_scan(path)
            self.run_scan(path,source_error=OSError('offline'))
            _,_,state,_=self.run_scan(path,source_error=OSError('offline'))
        health=state['source_health']['Audit']
        self.assertEqual(health['consecutive_failures'],2)
        self.assertEqual(health['last_list_success_at'],old['source_health']['Audit']['last_list_success_at'])
        self.assertEqual(health['content_coverage'],'unverified')

    def test_safe_error_never_includes_sensitive_url(self):
        err=HTTPError('https://secret.example/token',403,'secret response',{},None)
        self.assertEqual(safe_error(err),'http_403')

    def test_preview_hides_profile_and_does_not_mutate_state(self):
        cfg=copy.deepcopy(CFG);cfg['candidate'].update(name='Private Example',kpss_score=82.345)
        output=io.StringIO()
        with tempfile.TemporaryDirectory() as d,patch.object(main,'scan_source',return_value=[ITEM]), \
             patch.object(main,'enrich',side_effect=lambda i:dict(i,text='P93 en az 60 puan')), \
             patch.object(main,'telegram_request') as sender,redirect_stdout(output):
            path=Path(d)/'s.json';path.write_text('{"sent":{}}');before=path.read_bytes()
            self.assertEqual(main.scan(cfg,SOURCES,path,True),0)
            self.assertEqual(path.read_bytes(),before)
            sender.assert_not_called()
        self.assertNotIn('82.345',output.getvalue())
        self.assertNotIn('Private Example',output.getvalue())

    def test_private_config_override_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'config.json').write_text('{"marker":"legacy"}')
            (root/'config.local.json').write_text('{"marker":"private"}')
            with patch.object(main,'BASE',root),patch.dict(os.environ,KPSS_CONFIG_FILE=''):
                self.assertEqual(main.load_config()['marker'],'private')
                self.assertEqual(main.load_config('config.json')['marker'],'legacy')
            with patch.object(main,'BASE',root),patch.dict(os.environ,KPSS_CONFIG_FILE='config.local.json'):
                self.assertEqual(main.load_config()['marker'],'private')

    def test_legacy_receipts_remain_intact_after_policy_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            _,_,first,_=self.run_scan(path)
            old=copy.deepcopy(first['sent'])
            first['notices'][ITEM['url']]['policy']='audit-v2'
            path.write_text(json.dumps(first))
            _,count,state,_=self.run_scan(path)
        self.assertEqual(count,0)
        self.assertEqual(state['sent'],old)


if __name__=='__main__':
    unittest.main()
