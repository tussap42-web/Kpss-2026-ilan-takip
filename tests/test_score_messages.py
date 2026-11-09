from datetime import date
import json
import unittest

import main
from eligibility import assess_notice

CFG = json.loads((main.BASE / 'config.json').read_text())
TODAY = date(2026, 11, 9)
ITEM = {'title': 'Örnek personel alımı', 'source': 'Test', 'url': 'https://example.org/job'}


class ScoreMessageTests(unittest.TestCase):
    def message(self, text, **kwargs):
        assessment = assess_notice(text, CFG, TODAY, **kwargs)
        return main.message_for(dict(ITEM, assessment=assessment), CFG), assessment

    def test_notice_threshold_is_separate_from_candidate_score(self):
        message, _ = self.message('KPSS P93 en az 60 puan. Ön lisans mezunları.')
        self.assertIn('Başvuru eşiği: En az 60 puan', message)
        self.assertIn('Mezuniyet / nitelik: Ön lisans', message)
        self.assertIn('Takip profili: P93 / yaklaşık 70,6', message)
        self.assertNotIn('Aday:', message)

    def test_each_position_keeps_own_score_type_threshold_and_education(self):
        rows = [['Unvan', 'KPSS Puan Türü', 'Taban Puan', 'Mezuniyet / Nitelik'],
                ['Büro personeli', 'P3', '75', 'Lisans 4001'],
                ['Destek personeli', 'P93', '60', 'Ön lisans 3001']]
        message, _ = self.message('İlan metni', rows=rows)
        office, support = message.split('Kadro: Destek personeli')
        self.assertIn('KPSS: P3 | Başvuru eşiği: En az 75 puan', office)
        self.assertIn('Lisans 4001', office)
        self.assertIn('KPSS: P93 | Başvuru eşiği: En az 60 puan', support)
        self.assertIn('Ön lisans 3001', support)

    def test_unknown_score_is_not_filled_with_candidate_score(self):
        message, _ = self.message('Personel alımı şartları ektedir.')
        self.assertIn('Başvuru eşiği: Doğrulanamadı', message)
        self.assertNotIn('En az 70', message)

    def test_ambiguous_scores_are_not_collapsed_to_lowest_threshold(self):
        message, _ = self.message('KPSS P93 en az 60 puan. KPSS P3 en az 75 puan.')
        self.assertIn('Kadroya göre değişiyor', message)
        self.assertNotIn('Başvuru eşiği: En az', message)

    def test_incomplete_document_does_not_present_score_as_verified(self):
        message, _ = self.message('KPSS P93 en az 60 puan', incomplete=True)
        self.assertIn('Belge eksik; puan şartı doğrulanamadı', message)
        self.assertNotIn('Başvuru eşiği: En az 60', message)

    def test_explicit_exemption_and_conflicting_condition_are_distinct(self):
        message, _ = self.message('KPSS şartı aranmaz.')
        self.assertIn('KPSS şartı: Aranmadığı belirtiliyor', message)
        message, _ = self.message('KPSS şartı aranmaz. P93 en az 60 puan.')
        self.assertNotIn('KPSS şartı: Aranmadığı belirtiliyor', message)

    def test_decimal_threshold_and_link_are_preserved(self):
        message, _ = self.message('KPSS P93 en az 70,50 puan')
        self.assertIn('En az 70,5 puan', message)
        self.assertTrue(message.endswith(ITEM['url']))
        self.assertIn('yerleşme veya kazanma puanı değildir', message)
