"""KPSS ilan takip - official homepages basic link monitor (Python stdlib)."""
from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor
from eligibility import assess
from details import enrich
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
DEFAULT_STATE = BASE / 'state' / 'seen.json'
AGENT = 'KPSS-Ilan-Takip/2.0 (public announcements monitor; contact: repository owner)'
CONNECT_TIMEOUT = 12
MAX_CONTENT = 1_500_000
MAX_PER_RUN = 8
JOB_PHRASES = (
    'personel alimi', 'personel alim', 'personel istihdami', 'personel istihdam',
    'personel ilan', 'personel temini', 'personel basvur',
    'isci alimi', 'isci alim', 'isci ilan', 'surekli isci',
    'memur alimi', 'memur alim', 'memur ilan',
    'sozlesmeli personel', 'destek personeli', 'buro personeli',
    'hizmetli alimi', 'hizmetli alim', 'kamu personeli alimi',
    'istihdam edilecek', 'kadro ilan',
    'merkezi yerlestirme', 'merkezi atama',
)
NOT_JOB = ('iletisim', 'gizlilik', 'cerez', 'privacy', 'contact', 'kvkk', 'hakkimizda', 'anasayfa')


def normalize(text: str) -> str:
    text = (text or '').translate(str.maketrans('İıŞşĞğÜüÖöÇç', 'IiSsGgUuOoCc'))
    text = text.lower()
    return re.sub(r'\s+', ' ', text).strip()


def is_job_link(title: str, url: str) -> bool:
    title = normalize(title)
    path = normalize(urlsplit(url).path.replace('-', ' ').replace('_', ' '))
    if not title or len(title) < 12:
        return False
    if any(x in title for x in ('sonuclari', 'sonuc duyurusu', 'iptal', 'basvurulari sona erdi', 'basvuru sona erdi', 'sinav sonucu')):
        return False
    if any(x in title for x in NOT_JOB):
        return False
    if any(x in title for x in JOB_PHRASES):
        return True
    # Do not treat a menu item named "İlanlar" or "Duyurular" as a job posting.
    if any(x in title for x in ('alim', 'basvuru', 'yerlestirme', 'tercih')):
        if any(x in title + ' ' + path for x in ('personel', 'isci', 'memur', 'kpss', 'istihdam', 'kadro', '4 b')):
            return True
    if 'kpss' in title and any(x in title for x in ('tercih', 'yerlestirme', 'atama')):
        return True
    return False


def canonical(url: str) -> str:
    sp = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(sp.query) if not k.lower().startswith(('utm_', 'fbclid', 'gclid'))])
    return urlunsplit((sp.scheme.lower(), sp.netloc.lower(), sp.path.rstrip('/') or '/', query, ''))


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.current_url: str | None = None
        self.current_parts: list[str] = []
        self.in_anchor = False
        self.ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ('script', 'style'):
            self.ignore_depth += 1
        if tag == 'a' and not self.in_anchor:
            self.in_anchor = True
            self.current_parts = []
            self.current_url = dict(attrs).get('href')

    def handle_data(self, data: str) -> None:
        if self.in_anchor and not self.ignore_depth:
            self.current_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ('script', 'style') and self.ignore_depth:
            self.ignore_depth -= 1
        if tag == 'a' and self.in_anchor:
            title = html.unescape(' '.join(self.current_parts))
            title = ' '.join(title.split())
            if self.current_url and title:
                self.links.append((self.current_url, title))
            self.current_url = None
            self.current_parts = []
            self.in_anchor = False


def get_html(url: str) -> str:
    req = Request(url, headers={'User-Agent': AGENT, 'Accept': 'text/html,application/xhtml+xml'})
    with urlopen(req, timeout=CONNECT_TIMEOUT) as response:
        mime = response.headers.get('Content-Type', '')
        if 'html' not in mime.lower():
            raise ValueError('HTML olmayan içerik')
        content = response.read(MAX_CONTENT + 1)
        if len(content) > MAX_CONTENT:
            raise ValueError('Sayfa boyutu limitin üzerinde')
        charset = response.headers.get_content_charset() or 'utf-8'
    try:
        return content.decode(charset, errors='replace')
    except LookupError:
        return content.decode('utf-8', errors='replace')


def scan_source(source: dict) -> list[dict]:
    url = source['url']
    body = get_html(url)
    parser = LinkParser()
    parser.feed(body)
    base_host = (urlsplit(url).hostname or '').removeprefix('www.')
    found: dict[str, dict] = {}
    for href, label in parser.links:
        link = urljoin(url, href)
        parts = urlsplit(link)
        if parts.scheme != 'https' or not parts.hostname:
            continue
        # Restrict monitor to the official site / its subdomains.
        if not (parts.hostname == base_host or parts.hostname.endswith('.' + base_host)):
            continue
        if not is_job_link(label, link):
            continue
        link = canonical(link)
        if link == canonical(url):
            continue
        found[link] = {'title': label[:220], 'url': link, 'source': source['name']}
    return list(found.values())


def get_state(path: Path) -> dict:
    if not path.exists():
        return {'sent': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data.get('sent'), dict):
        raise ValueError('state/seen.json yanlış biçimde')
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(path)


def telegram_request(token: str, method: str, payload: dict) -> dict:
    # Never print the URL or unfiltered HTTP exception: it contains the bot token.
    body = json.dumps(payload).encode('utf-8')
    req = Request(f'https://api.telegram.org/bot{token}/{method}', data=body,
                  headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(req, timeout=CONNECT_TIMEOUT) as response:
            data = json.loads(response.read(100_000).decode('utf-8'))
    except HTTPError as err:
        raise RuntimeError(f'Telegram HTTP {err.code}') from None
    except URLError:
        raise RuntimeError('Telegram sunucusuna bağlanılamadı') from None
    if not data.get('ok'):
        raise RuntimeError('Telegram API isteği başarısız (ayrıntı gizlendi)')
    return data


def parse_chats(value: str) -> list[str]:
    chats = [x.strip() for x in value.split(',') if x.strip()]
    if any(not re.fullmatch(r'-?\d+', chat) for chat in chats):
        raise ValueError('TELEGRAM_CHAT_IDS sadece virgülle ayrılmış sayısal kimlikler içermeli')
    return list(dict.fromkeys(chats))


def chat_fingerprint(token: str, chat_id: str) -> str:
    # Keep chat IDs out of the state file, which may be committed to a public repository.
    return hashlib.sha256(f'{token}:{chat_id}'.encode('utf-8')).hexdigest()[:24]


def message_for(item: dict, config: dict) -> str:
    assessment = item.get('assessment', {})
    priority = config['search_settings']['priority_city']
    city = f'📍 {priority} öncelikli\n' if normalize(priority) in normalize(item['title']) else ''
    heading = '⏰ SON BAŞVURU HATIRLATMASI' if item.get('reminder') else '📢 İNCELENECEK İLAN'
    lines = [heading, city + 'Kaynak: ' + item['source'], item['title'],
             f"Aday: {config['candidate']['kpss_score_type']} / {config['candidate']['kpss_score']}"]
    if assessment.get('deadline'):
        lines.append('Son başvuru: ' + assessment['deadline'])
    if item.get('detail_warning'):
        lines.append(item['detail_warning'])
    lines.extend('• ' + warning for warning in assessment.get('warnings', []))
    lines.extend(['Şartların tamamı doğrulanmış değildir; resmî ilanı kontrol et.', item['url']])
    return '\n'.join(lines)[:4050]


def find_chats(token: str) -> int:
    data = telegram_request(token, 'getUpdates', {'limit': 100, 'timeout': 0})
    ids = {}
    for update in data.get('result', []):
        msg = update.get('message') or update.get('edited_message') or {}
        chat = msg.get('chat', {})
        if chat.get('id') is not None:
            ids[str(chat['id'])] = chat.get('type', 'bilinmiyor')
    if not ids:
        print('Sohbet bulunamadı. Bota Telegram’dan /start gönderip yeniden dene.')
    else:
        for chat_id, kind in ids.items():
            print(f'CHAT_ID={chat_id} (tür={kind})')
        print('Bu kimlikleri TELEGRAM_CHAT_IDS adlı GitHub Actions secret alanına kaydet.')
    return 0


def scan(config: dict, sources: dict, state_path: Path, dry_run: bool) -> int:
    zone = ZoneInfo(config.get('schedule', {}).get('timezone', 'Europe/Istanbul'))
    now = datetime.now(zone)
    start = int(config['schedule']['start_hour'])
    end = int(config['schedule']['end_hour'])
    if not dry_run and os.getenv('FORCE_RUN') != '1' and not (start <= now.hour <= end):
        print(f'Saat {now:%H:%M}; saat aralığı dışı, tarama atlandı.')
        return 0
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    try:
        chat_ids = parse_chats(os.getenv('TELEGRAM_CHAT_IDS', ''))
    except ValueError as exc:
        print(exc)
        return 1
    if not dry_run and not config['notifications'].get('enabled', False):
        print('Bildirimler kapalı; işlem atlandı.')
        return 0
    if not dry_run and (not token or not chat_ids):
        print('Telegram ayarlanmadı: Bildirim gönderilmedi, kayıt değişmedi. Secrets kontrol et.')
        return 1
    all_items = {}
    failures = 0
    for source in sources['sources']:
        try:
            links = scan_source(source)
            print(f'{source["name"]}: {len(links)} ilan adayı bağlantı')
            for item in links:
                all_items[item['url']] = item
        except (OSError, ValueError, HTTPError, URLError) as exc:
            failures += 1
            # The error can include URLs: print only a safe class/HTTP status, not secrets.
            error = f'HTTP {exc.code}' if isinstance(exc, HTTPError) else type(exc).__name__
            print(f'{source["name"]}: erişim/parsing hatası ({error})')
    if failures == len(sources['sources']):
        print('HATA: Hiçbir kaynak taranamadı. Bu çalıştırma başarılı sayılmadı.')
        return 1
    state = get_state(state_path)
    notices = state.setdefault('notices', {})
    settings = config['notifications']
    # A short cache avoids repeatedly downloading the same attachments every hour.
    policy = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    p = normalize(config['search_settings']['priority_city'])
    candidates = sorted(all_items.values(), key=lambda d: (p not in normalize(d['title']), d['url']))
    refresh = []
    for item in candidates:
        cached = notices.get(item['url'])
        if not cached or cached.get('checked_on') != now.date().isoformat() or cached.get('policy') != policy:
            refresh.append(item)
    limit = max(1, min(int(config.get('processing', {}).get('max_details_per_run', 12)), 30))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item in pool.map(enrich, refresh[:limit]):
            assessment = assess(item.get('text', ''), config, now.date())
            if item.get('detail_warning'):
                # Incomplete attachment sets cannot safely exclude an entire announcement.
                assessment['status'] = 'review'
                assessment['reasons'] = []
                assessment['deadline'] = None
            item.pop('text', None)
            item.update(assessment=assessment, checked_on=now.date().isoformat(), policy=policy)
            notices[item['url']] = item
    queued = []
    for item in candidates:
        cached = notices.get(item['url'])
        if not cached or cached.get('policy') != policy:
            continue  # Leave for a later run, never send unassessed backlog as matched.
        assessment = cached['assessment']
        if assessment['status'] == 'excluded':
            print('Elendi: ' + item['title'] + ': ' + '; '.join(assessment['reasons']))
            continue
        if assessment.get('deadline') and assessment['deadline'] < now.date().isoformat() and config['search_settings'].get('only_active_applications', True):
            continue
        if settings.get('notify_new_jobs', True):
            queued.append((cached, canonical(item['url'])))
    # Retain known notices so reminders survive disappearance from the homepage.
    if settings.get('notify_deadline_approaching', True):
        for url, item in notices.items():
            assessment = item['assessment']
            deadline = assessment.get('deadline')
            if not deadline or assessment['status'] == 'excluded' or item.get('policy') != policy:
                continue
            days = (date.fromisoformat(deadline) - now.date()).days
            if days in settings.get('deadline_reminder_days', [3, 1, 0]):
                # Never remind before the recipient has received the original notice.
                original = state['sent'].get(canonical(url), {})
                if original:
                    queued.append((dict(item, reminder=True, original_chats=original.get('chats', [])),
                                   canonical(url) + '#reminder:' + deadline + ':' + str(days)))
    count, errors = 0, 0
    cap = max(1, min(int(settings.get('max_notifications_per_run', 20)), 100))
    for item, key in queued:
        completed = set(state['sent'].get(key, {}).get('chats', []))
        if dry_run:
            if key not in state['sent']:
                print('[ÖN İZLEME] ' + message_for(item, config))
            continue
        recipients = [chat for chat in chat_ids if chat_fingerprint(token, chat) not in completed
                      and (not item.get('reminder') or chat_fingerprint(token, chat) in item.get('original_chats', []))]
        if not recipients:
            continue
        if count >= cap:
            break
        for chat_id in recipients:
            try:
                telegram_request(token, 'sendMessage', {'chat_id': chat_id, 'text': message_for(item, config), 'disable_web_page_preview': True})
            except RuntimeError as exc:
                print(f'Bildirim başarısız: {exc}')
                errors += 1
                continue  # One unavailable recipient must not block other recipients.
            completed.add(chat_fingerprint(token, chat_id))
            state['sent'][key] = {'title': item['title'], 'chats': sorted(completed), 'at': now.isoformat()}
            save_state(state_path, state)
        count += 1
    if not dry_run:
        save_state(state_path, state)
    print(f'Kaynak: {len(sources["sources"])} | Kaynak hatası: {failures} | Bildirim denemesi: {count} | Bekleyen ayrıntı: {max(0, len(refresh)-limit)}')
    if failures:
        print('::warning::Bazı kaynaklar okunamadı; tarama eksik. Actions günlüklerini kontrol et.')
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description='KPSS ilan adayı takip aracı')
    parser.add_argument('--mode', choices=('scan', 'preview', 'find-chats', 'test-message'), default='scan')
    parser.add_argument('--state-file', type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    config = json.loads((BASE / 'config.json').read_text(encoding='utf-8'))
    sources = json.loads((BASE / 'sources.json').read_text(encoding='utf-8'))
    if args.mode == 'find-chats':
        token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        if not token:
            print('TELEGRAM_BOT_TOKEN secret bulunamadı')
            return 1
        return find_chats(token)
    if args.mode == 'test-message':
        token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        chats = parse_chats(os.getenv('TELEGRAM_CHAT_IDS', ''))
        if not token or not chats:
            print('Test için TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_IDS gerekli')
            return 1
        for chat in chats:
            telegram_request(token, 'sendMessage', {'chat_id': chat, 'text': '✅ KPSS İlan Takip bağlantı testi başarılı.'})
        print(f'Test mesajı gönderilen sohbet: {len(chats)}')
        return 0
    return scan(config, sources, args.state_file, dry_run=args.mode == 'preview')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, json.JSONDecodeError, FileNotFoundError, RuntimeError) as exc:
        print(f'Program hatası: {exc}')
        sys.exit(1)

