from datetime import date
import json
import unittest

import main
from eligibility import assess_notice
from notification_policy import notification_matches

CFG = json.loads((main.BASE / 'config.json').read_text())
TODAY = date(2026, 11, 9)
ITEM = {'title': 'Örnek personel alımı', 'source': 'Test',
        'url': 'https://example.org/job'}


class ScoreMessageTests(unittest.TestCase):
    def item(self, text, **kwargs):
        assessment = assess_notice(text, CFG, TODAY, **kwargs)
        return dict(ITEM, assessment=assessment)

    def test_verified_70_sends_clear_message(self):
        item = self.item('KPSS P93 en az 70 puan. Ön lisans 3001')
        self.assertEqual(notification_matches(item, CFG), [])
        message = main.message_for(item, CFG)
        self.assertIn('KPSS P93 asgari başvuru puanı: 70', message)
        self.assertIn('Ön lisans, 3001', message)
        self.assertNotIn('doğrulanamadı', message.lower())
        self.assertNotIn('Takip profili', message)
        self.assertNotIn('kazanma puanı', message)
        self.assertTrue(message.endswith(ITEM['url']))

    def test_below_70_is_eligible(self):
        item = self.item('KPSS P93 en az 65 puan. 3001')
        self.assertIsNotNone(notification_matches(item, CFG))

    def test_p93_threshold_at_most_approximate_70_60(self):
        for threshold in ('70,01', '70,60'):
            with self.subTest(threshold=threshold):
                item = self.item(f'KPSS P93 en az {threshold} puan. 3001')
                self.assertIsNotNone(notification_matches(item, CFG))
        for threshold in ('70,61', '71', '75'):
            with self.subTest(threshold=threshold):
                item = self.item(f'KPSS P93 en az {threshold} puan. 3001')
                self.assertIsNone(notification_matches(item, CFG))
                with self.assertRaises(ValueError):
                    main.message_for(item, CFG)

    def test_unknown_kpss_does_not_notify(self):
        for text in ('Başvuru şartları ektedir.', 'P93 puan türü ile başvurulur.',
                     'KPSS şartı aranmaz.', 'P3 en az 60 puan. 3001'):
            with self.subTest(text=text):
                self.assertIsNone(notification_matches(self.item(text), CFG))

    def test_no_kpss_worker_review_alert_but_not_p94(self):
        from notification_policy import no_kpss_worker_matches
        item = self.item('KPSS şartı aranmaz. En az lise mezunu olmak gerekir.')
        item['title'] = 'Sürekli işçi alımı'
        self.assertTrue(no_kpss_worker_matches(item, CFG))
        self.assertIn('İNCELE', main.message_for(item, CFG))
        for phrase in ('P94 en az 60 puan. En az lise mezunu olmak gerekir.',
                       'KPSS şartı aranmaz. Lise mezunu olmak gerekir.',
                       'En az lise mezunu olmak gerekir.'):
            with self.subTest(phrase=phrase):
                item = self.item(phrase)
                item['title'] = 'Sürekli işçi alımı'
                self.assertFalse(no_kpss_worker_matches(item, CFG))

    def test_sbb_source_is_configured(self):
        sources = json.loads((main.BASE / 'sources.json').read_text())
        self.assertIn('https://kamuilan.sbb.gov.tr/', [s['url'] for s in sources['sources']])

    def test_score_without_matching_education_does_not_notify(self):
        self.assertIsNone(notification_matches(self.item('P93 en az 70 puan'), CFG))
        self.assertIsNone(notification_matches(
            self.item('P93 en az 70 puan. Lisans mezunu olmak'), CFG))

    def test_position_filter_hides_higher_score_row(self):
        rows = [['Unvan', 'KPSS Puan Türü', 'Taban Puan', 'Mezuniyet / Nitelik'],
                ['Büro personeli', 'P93', '75', 'Ön lisans 3001'],
                ['Destek personeli', 'P93', '70', 'Ön lisans 3001']]
        item = self.item('İlan metni', rows=rows)
        matched = notification_matches(item, CFG)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]['title'], 'destek personeli')
        message = main.message_for(item, CFG)
        self.assertIn('Destek personeli', message)
        self.assertNotIn('Büro personeli', message)
        self.assertNotIn('75', message)

    def test_partial_document_never_notifies(self):
        item = self.item('P93 en az 70 puan. Ön lisans 3001', incomplete=True)
        self.assertIsNone(notification_matches(item, CFG))

    def test_reminder_has_same_gate(self):
        item = self.item('P93 en az 70 puan. Ön lisans 3001')
        item['reminder'] = True
        self.assertIn('HATIRLATMASI', main.message_for(item, CFG))


if __name__ == '__main__':
    unittest.main()
