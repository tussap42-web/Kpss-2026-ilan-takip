import copy
import json
from datetime import date
import unittest
import main
from eligibility import assess
from details import enrich, TextParser
from unittest.mock import patch

CFG = json.loads((main.BASE / 'config.json').read_text())
TODAY = date(2026, 11, 8)


class EligibilityTests(unittest.TestCase):
    def check(self, text):
        return assess(text, CFG, TODAY)

    def test_above_score_excluded(self):
        a = self.check('2026 KPSS P93 puan türünden en az 75 puan almış olmak')
        self.assertEqual(a['status'], 'excluded')
        self.assertEqual(a['minimum_score'], 75)

    def test_score_pass_still_review(self):
        a = self.check('KPSSP93 puan türünden en az 70 puan. 3001')
        self.assertEqual(a['status'], 'review')
        self.assertEqual(a['minimum_score'], 70)

    def test_decimal_score(self):
        self.assertEqual(self.check('P93 en az 70,61 puan')['status'], 'excluded')
        self.assertEqual(self.check('P93 en az 70.60 puan')['status'], 'review')

    def test_different_type(self):
        self.assertEqual(self.check('P3 en az 60 puan')['status'], 'excluded')

    def test_mixed_types_not_excluded(self):
        a = self.check('KPSSP3 en az 80 puan; KPSSP93 en az 75 puan')
        self.assertEqual(a['status'], 'review')
        self.assertIsNone(a['minimum_score'])

    def test_multiple_thresholds_same_type(self):
        self.assertEqual(self.check('P93 en az 65 puan; P93 en az 80 puan')['status'], 'review')

    def test_unlabeled_number_is_not_score(self):
        self.assertIsNone(self.check('P93 75 kişi alınacak, yaş 35')['minimum_score'])

    def test_expired_and_today(self):
        self.assertEqual(self.check('Son başvuru tarihi: 07.11.2026')['status'], 'excluded')
        self.assertEqual(self.check('Son başvuru: 08/11/2026')['status'], 'review')

    def test_multiple_deadlines(self):
        self.assertIsNone(self.check('Son başvuru: 07.11.2026 Son başvuru: 20.11.2026')['deadline'])

    def test_invalid_date(self):
        self.assertIsNone(self.check('Son başvuru: 31.02.2026')['deadline'])

    def test_no_kpss_setting(self):
        c = copy.deepcopy(CFG)
        c['search_settings']['include_jobs_without_kpss'] = False
        self.assertEqual(assess('KPSS şartı aranmaz', c, TODAY)['status'], 'excluded')
        self.assertEqual(self.check('KPSS şartı aranmaz')['status'], 'review')

    def test_unreadable_details(self):
        with patch('details.read_document', side_effect=ValueError('test')):
            item = enrich({'url':'https://example.org/x'})
        self.assertEqual(item['text'], '')
        self.assertIn('detail_warning', item)

    def test_text_parser_ignores_script(self):
        p = TextParser()
        p.feed('<script>P93 en az 99</script><p>Gerçek ilan</p><a href="x.pdf">Belge</a>')
        self.assertNotIn('99', ' '.join(p.parts))
        self.assertEqual(p.pdfs, ['x.pdf'])

    def test_results_and_cancellations_ignored(self):
        for title in ['2024 yılı personel alımı sonuçları', 'Personel alımı iptal duyurusu', 'Personel alımı başvuruları sona erdi']:
            self.assertFalse(main.is_job_link(title, 'https://example.org/x'))

    def test_age_is_not_score(self):
        self.assertIsNone(self.check('P93 sınavına girmiş olmak, en az 35 yaşında olmak')['minimum_score'])

    def test_conflicting_kpss_exemption_is_ambiguous(self):
        self.assertEqual(self.check('KPSS şartı aranmaz; P93 en az 90 puan')['status'], 'review')

    def test_degree_requirement(self):
        self.assertEqual(self.check('Lisans mezunu olmak')['status'], 'excluded')
        self.assertEqual(self.check('Ön lisans veya lisans mezunu olmak')['status'], 'review')

    def test_old_year_requires_review(self):
        self.assertTrue(any('yıl' in w for w in self.check('2024 KPSS P93 en az 60 puan')['warnings']))

    def test_scanned_pdf_requires_review(self):
        import io
        from email.message import Message
        from pypdf import PdfWriter
        from details import read_document
        writer=PdfWriter()
        writer.add_blank_page(width=72,height=72)
        stream=io.BytesIO()
        writer.write(stream)
        response=io.BytesIO(stream.getvalue())
        response.headers=Message()
        response.headers['Content-Type']='application/pdf'
        response.geturl=lambda:'https://example.org/doc.pdf'
        with patch('details.fetch_bytes',return_value=(response.getvalue(), response.headers, response.geturl())):
            with self.assertRaisesRegex(ValueError,'OCR'):
                read_document('https://example.org/doc.pdf')

    def test_www_and_external_pdf_links(self):
        import io
        from email.message import Message
        from details import read_document
        response=io.BytesIO(b'<p>Job</p><a href="/job.pdf">PDF</a><a href="https://evil.example/job.pdf">PDF</a>')
        response.headers=Message()
        response.headers['Content-Type']='text/html; charset=utf-8'
        response.geturl=lambda:'https://www.example.org/job'
        with patch('details.fetch_bytes',return_value=(response.getvalue(), response.headers, response.geturl())):
            text,pdfs=read_document('https://www.example.org/job')
        self.assertEqual(pdfs,['https://www.example.org/job.pdf'])
