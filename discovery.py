"""Bounded announcement navigation, advertised feeds and explicit next-page links."""
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
import xml.etree.ElementTree as ET


def canonical(url):
    p = urlsplit(url)
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith(('utm_', 'fbclid', 'gclid')))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', urlencode(query), ''))


def same_site(url, root):
    host = (urlsplit(url).hostname or '').removeprefix('www.')
    base = (urlsplit(root).hostname or '').removeprefix('www.')
    return urlsplit(url).scheme == 'https' and (host == base or host.endswith('.' + base))


class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.feeds = [], []
        self.current = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'link' and attrs.get('type') in ('application/rss+xml', 'application/atom+xml'):
            self.feeds.append(attrs.get('href', ''))
        if tag == 'a' and not self.skip:
            self.current = [attrs.get('href', ''), [], attrs.get('rel', ''), attrs.get('title', '')]

    def handle_data(self, value):
        if self.current and not self.skip:
            self.current[1].append(value)

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1
        if tag == 'a' and self.current:
            href, parts, rel, title = self.current
            self.links.append((href, ' '.join(parts).strip() or title, rel))
            self.current = None


def scan_source(source, get_html, is_job_link, normalize):
    seeds = source.get('entrypoints', []) + [source['url']]
    pending = deque(dict.fromkeys(seeds))
    visited, found, errors = set(), {}, []
    limit = min(max(int(source.get('max_pages', 4)), 1), 6)
    ok = 0
    while pending and len(visited) < limit:
        url = canonical(pending.popleft())
        if url in visited or not same_site(url, source['url']):
            continue
        visited.add(url)
        try:
            body = get_html(url)
            if '<rss' in body[:500] or '<feed' in body[:500]:
                root = ET.fromstring(body)
                entries = root.findall('.//item') + root.findall('.//{http://www.w3.org/2005/Atom}entry')
                links = []
                for entry in entries[:200]:
                    title = entry.findtext('title') or entry.findtext('{http://www.w3.org/2005/Atom}title') or ''
                    link = entry.findtext('link')
                    if not link:
                        node = entry.find('{http://www.w3.org/2005/Atom}link')
                        link = node.get('href', '') if node is not None else ''
                    links.append((link, title, ''))
                feeds = []
            else:
                parser = IndexParser()
                parser.feed(body)
                links, feeds = parser.links, parser.feeds
            ok += 1
            for href in feeds:
                link = urljoin(url, href)
                if same_site(link, source['url']):
                    pending.append(link)
            for href, label, rel in links:
                link = canonical(urljoin(url, href))
                if not same_site(link, source['url']) or link == url:
                    continue
                if is_job_link(label, link):
                    found[link] = {'title': label[:220], 'url': link, 'source': source['name']}
                # Follow observed navigation only; don't guess an API or page number.
                name = normalize(label)
                if (name in ('duyurular', 'ilanlar', 'personel alimi', 'tum duyurular', 'tumu', 'sonraki', 'sonraki sayfa', 'next', '›', '»')
                        or 'next' in rel.split()):
                    if link not in visited and link not in pending:
                        pending.append(link)
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append(type(exc).__name__)
    source['_scan_stats'] = {'pages_ok': ok, 'pages_failed': len(errors), 'errors': errors,
                             'pages_attempted': len(visited), 'navigation_pending': len(pending)}
    if not ok:
        raise OSError('Hiçbir liste sayfası okunamadı')
    return list(found.values())
