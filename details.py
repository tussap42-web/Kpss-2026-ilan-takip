"""Preserve line/table boundaries and mark truncated/unreadable attachments explicitly."""
import hashlib
import io
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from network import fetch_bytes

MAX_BYTES = 4_000_000


class DocumentText(str):
    def __new__(cls, value, incomplete=False, pdf_digest=None):
        obj = str.__new__(cls, value)
        obj.incomplete, obj.pdf_digest = incomplete, pdf_digest
        return obj


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.pdfs = [], []
        self.skip = 0
        self.incomplete = False
        self.article_parts = []
        self.in_article = 0
        self.heading_tag = None
        self.headings = []
        self.heading_parts = []

    def append(self, text):
        self.parts.append(text)
        if self.in_article:
            self.article_parts.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'nav', 'footer', 'header'):
            self.skip += 1
        if self.skip:
            return
        if tag in ('main', 'article'):
            self.in_article += 1
        if tag == 'h1':
            self.heading_tag, self.heading_parts = tag, []
        if tag in ('p', 'div', 'br', 'tr', 'h1', 'h2', 'h3', 'li'):
            self.append('\n')
        if tag in ('td', 'th'):
            self.append('\t')
            if attrs.get('rowspan', '1') != '1' or attrs.get('colspan', '1') != '1':
                self.incomplete = True
        href = attrs.get('href', '')
        if tag == 'a' and urlsplit(href).path.lower().endswith('.pdf'):
            self.pdfs.append(href)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer', 'header') and self.skip:
            self.skip -= 1
        if self.skip:
            return
        if tag == self.heading_tag:
            self.headings.append(' '.join(self.heading_parts))
            self.heading_tag = None
        if tag in ('p', 'div', 'tr', 'li'):
            self.append('\n')
        if tag in ('main', 'article') and self.in_article:
            self.in_article -= 1

    def handle_data(self, data):
        if not self.skip:
            self.append(data)
            if self.heading_tag:
                self.heading_parts.append(data)


def read_document(url):
    body, headers, final_url = fetch_bytes(url, MAX_BYTES)
    mime = headers.get('Content-Type', '').lower()
    if 'pdf' in mime or body.startswith(b'%PDF-'):
        text, incomplete = extract_pdf(body)
        return DocumentText(text, incomplete, hashlib.sha256(body).hexdigest()), []
    if 'html' not in mime:
        raise ValueError('Desteklenmeyen belge türü')
    parser = TextParser()
    charset = headers.get_content_charset() or 'utf-8'
    try:
        parser.feed(body.decode(charset, errors='replace'))
    except LookupError:
        parser.feed(body.decode('utf-8', errors='replace'))
    host = (urlsplit(final_url).hostname or '').removeprefix('www.')
    pdfs, blocked = [], 0
    for href in parser.pdfs:
        link = urljoin(final_url, href)
        target = urlsplit(link)
        if target.scheme == 'https' and (target.hostname == host or (target.hostname or '').endswith('.' + host)):
            pdfs.append(link)
        else:
            blocked += 1
    pdfs = list(dict.fromkeys(pdfs))
    # Prefer semantic article content; do not interpret navigation/sidebar job lists as this notice.
    text = ''.join(parser.article_parts or parser.parts)
    document = DocumentText(text[:180_000], parser.incomplete or len(text) > 180_000 or len(pdfs) > 2 or blocked > 0)
    document.heading = ' '.join(parser.headings)[:1000]
    return document, pdfs[:2]


def enrich(item):
    result = {key: item[key] for key in ('title', 'url', 'source') if key in item}
    warnings, digests, texts = [], [], []
    try:
        text, pdfs = read_document(item['url'])
        texts.append(text)
        if getattr(text, 'heading', ''):
            result['document_title'] = text.heading
        if getattr(text, 'incomplete', False):
            warnings.append('Belge/eklerin tamamı güvenle okunamadı')
        if getattr(text, 'pdf_digest', None):
            digests.append(text.pdf_digest)
        for pdf in pdfs:
            try:
                extra, _ = read_document(pdf)
                texts.append(extra)
                if getattr(extra, 'incomplete', False):
                    warnings.append('PDF kısmen okunabildi')
                if getattr(extra, 'pdf_digest', None):
                    digests.append(extra.pdf_digest)
            except Exception as exc:
                warnings.append(f'PDF okunamadı ({type(exc).__name__})')
        result['text'] = '\n'.join(texts)
        # Match the COMPLETE PDF set, not a shared conditions annex alone.
        if digests and len(digests) == (len(pdfs) or 1) and not warnings:
            result['document_key'] = 'pdf:' + hashlib.sha256(('|'.join(sorted(digests)) + ('|' + re.sub(r'\s+', ' ', item.get('title', '')).casefold() if pdfs else '')).encode()).hexdigest()
        else:
            clean = re.sub(r'\s+', ' ', result['text']).strip()
            if len(clean) >= 200 and not warnings:
                result['document_key'] = 'text:' + hashlib.sha256(clean.encode()).hexdigest()
        result['rows'] = [re.split(r'\t+| {2,}', line.strip()) for line in result['text'].splitlines()
                          if '\t' in line or re.search(r' {2,}', line.strip())]
    except Exception as exc:
        result['text'] = ''
        warnings.append(f'İlan ayrıntıları okunamadı ({type(exc).__name__})')
    if warnings:
        result['detail_warning'] = '; '.join(dict.fromkeys(warnings))
    return result


def _pdf_worker(body, connection):
    try:
        import sys
        if sys.platform == 'linux':
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        if len(reader.pages) > 40:
            raise ValueError('PDF sayfa sınırı aşıldı')
        parts, incomplete = [], False
        for page in reader.pages:
            stream = page.get_contents()
            if stream and len(stream.get_data()) > 8_000_000:
                raise ValueError('PDF içerik sınırı aşıldı')
            text = page.extract_text(extraction_mode='layout') or ''
            incomplete |= len(text.strip()) < 20
            parts.append(text)
        text = '\n'.join(parts)
        if len(text.strip()) < 40:
            raise ValueError('PDF taranmış olabilir; OCR gerekli')
        connection.send((text[:180_000], incomplete or len(text) > 180_000, None))
    except Exception as exc:
        connection.send(('', True, str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
    finally:
        connection.close()


def extract_pdf(body):
    # A broken/huge PDF cannot hold up all delivery workers or exhaust runner memory.
    import multiprocessing
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_pdf_worker, args=(body, child))
    process.start()
    child.close()
    try:
        if not parent.poll(20):
            raise ValueError('PDF okuma süresi aşıldı')
        try:
            text, incomplete, error = parent.recv()
        except EOFError:
            raise ValueError('PDF okuyucu kaynak sınırında durdu') from None
        if error:
            raise ValueError(error)
        return text, incomplete
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()
