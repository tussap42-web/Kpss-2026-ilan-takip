"""Read bounded public notice pages and text PDFs without executing page scripts."""
import io
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

MAX_BYTES = 4_000_000


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.pdfs = [], []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'footer'):
            self.skip += 1
        href = dict(attrs).get('href', '')
        if tag == 'a' and urlsplit(href).path.lower().endswith('.pdf'):
            self.pdfs.append(href)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def read_document(url):
    if urlsplit(url).scheme != 'https':
        raise ValueError('HTTPS gerekli')
    req = Request(url, headers={'User-Agent': 'KPSS-Ilan-Takip/2.0', 'Accept': 'text/html,application/pdf'})
    with urlopen(req, timeout=12) as response:
        body = response.read(MAX_BYTES + 1)
        mime = response.headers.get('Content-Type', '').lower()
        charset = response.headers.get_content_charset() or 'utf-8'
        final_url = response.geturl()
    if len(body) > MAX_BYTES:
        raise ValueError('Belge boyutu sınırı aşıldı')
    if 'pdf' in mime or body.startswith(b'%PDF-'):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        if len(reader.pages) > 40:
            raise ValueError('PDF sayfa sınırı aşıldı')
        parts = []
        for page in reader.pages:
            stream = page.get_contents()
            if stream and len(stream.get_data()) > 8_000_000:
                raise ValueError('PDF içerik sınırı aşıldı')
            parts.append(page.extract_text() or '')
        text = '\n'.join(parts)
        if len(text.strip()) < 40:
            raise ValueError('PDF taranmış olabilir; OCR gerekli')
        return text[:180_000], []
    if 'html' not in mime:
        raise ValueError('Desteklenmeyen belge türü')
    parser = TextParser()
    try:
        parser.feed(body.decode(charset, errors='replace'))
    except LookupError:
        parser.feed(body.decode('utf-8', errors='replace'))
    host = (urlsplit(final_url).hostname or '').removeprefix('www.')
    pdfs = []
    for href in parser.pdfs:
        link = urljoin(final_url, href)
        target = urlsplit(link)
        if target.scheme == 'https' and (target.hostname == host or (target.hostname or '').endswith('.' + host)):
            pdfs.append(link)
    return ' '.join(parser.parts)[:180_000], list(dict.fromkeys(pdfs))[:2]


def enrich(item):
    result = dict(item)
    try:
        text, pdfs = read_document(item['url'])
        for pdf in pdfs:
            try:
                extra, _ = read_document(pdf)
                text += '\n' + extra
            except Exception as exc:
                result['detail_warning'] = f'PDF okunamadı ({type(exc).__name__})'
        result['text'] = text
    except Exception as exc:
        # Never mark unreadable documents as eligible or exclude them by guesswork.
        result['text'] = ''
        result['detail_warning'] = f'İlan ayrıntıları okunamadı ({type(exc).__name__})'
    return result
