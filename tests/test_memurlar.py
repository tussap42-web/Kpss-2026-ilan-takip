import json
import unittest
from pathlib import Path
from discovery import scan_source
from main import is_job_link, normalize

class MemurlarTests(unittest.TestCase):
    def test_source_and_explicit_notice_links(self):
        sources = json.loads((Path(__file__).resolve().parents[1] / 'sources.json').read_text())
        source = next(s for s in sources['sources'] if s['name'] == 'Memurlar.net')
        body = '''<a href="//ilan.memurlar.net/ilan/86850/jandarma-30-memur-alacak.html">Jandarma 30 Memur Alacak</a>
        <a href="/ilan/86927/sakarya-1-isci-alacak.html">Sakarya 1 İşçi Alacak</a>
        <a href="/haber/123/personel-alacak.html">Kurum personel alacak</a>
        <a href="/ilan/123/personel-alacak-iptal.html">Personel alacak ilanı iptal</a>
        <a href="https://outside.example/ilan/123/memur-alacak.html">Kurum memur alacak</a>'''
        items = scan_source(dict(source), lambda url: body, is_job_link, normalize)
        self.assertEqual({i['url'] for i in items}, {
            'https://ilan.memurlar.net/ilan/86850/jandarma-30-memur-alacak.html',
            'https://ilan.memurlar.net/ilan/86927/sakarya-1-isci-alacak.html'})
        self.assertTrue(all(i['source'] == 'Memurlar.net' for i in items))

    def test_other_sources_do_not_use_memurlar_rule(self):
        source = {'name': 'Test', 'url': 'https://example.org/'}
        items = scan_source(source, lambda url: '<a href="/ilan/123/30-memur-alacak.html">Kurum 30 Memur Alacak</a>', is_job_link, normalize)
        self.assertEqual(items, [])
