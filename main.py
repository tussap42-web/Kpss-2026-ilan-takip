"""Conservative KPSS notice monitoring with bounded discovery and durable receipts."""
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
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor
from eligibility import assess_notice
from discovery import scan_source as discover_source, canonical
from network import fetch_bytes, SourceAccessError, SourceContentError
from details import enrich
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
DEFAULT_STATE = BASE / 'state' / 'seen.json'
AGENT = 'KPSS-Ilan-Takip/2.0 (public announcements monitor; contact: repository owner)'
CONNECT_TIMEOUT = 12
MAX_CONTENT = 8_000_000
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
    if title in ('personel alimi', 'personel alim ilanlari', 'kamu personeli alim ilanlari', 'memur alimi', 'duyurular', 'ilanlar'):
        return False
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
    content, headers, _ = fetch_bytes(url, MAX_CONTENT)
    mime = headers.get('Content-Type', '').lower()
    if not any(kind in mime for kind in ('html', 'xml', 'rss', 'atom')):
        raise SourceContentError('unsupported_listing_content_type')
    if any(marker in content.lower() for marker in (b'cf-chl-', b'verify you are human', b'enable javascript and cookies to continue')):
        raise SourceAccessError('human_verification_required')
    charset = headers.get_content_charset() or 'utf-8'
    try:
        return content.decode(charset, errors='replace')
    except LookupError:
        return content.decode('utf-8', errors='replace')


def scan_source(source: dict) -> list[dict]:
    return discover_source(source, get_html, is_job_link, normalize)


def get_state(path: Path) -> dict:
    if not path.exists():
        return {'sent': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('sent'), dict):
        raise ValueError('state/seen.json yanlış biçimde')
    for name in ('notices', 'documents', 'aliases'):
        if name in data and not isinstance(data[name], dict):
            raise ValueError('Durum dosyası yanlış biçimde: ' + name)
    for record in data['sent'].values():
        if not isinstance(record, dict) or not isinstance(record.get('chats', []), list) or any(not isinstance(chat, str) for chat in record.get('chats', [])):
            raise ValueError('Bildirim geçmişi bozuk; dosya değiştirilmedi')
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as handle:
        handle.write(json.dumps(state, indent=2, ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
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
    except (OSError, ValueError):
        raise RuntimeError('Telegram isteği tamamlanamadı; teslimat belirsiz olabilir') from None
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


def requirement_lines(assessment: dict, education: str = '') -> list[str]:
    kinds = ', '.join(assessment.get('score_types', [])) or 'Doğrulanamadı'
    conditions = assessment.get('score_conditions', [])
    if not assessment.get('evidence_complete', False):
        score = 'KPSS şartı: Belge eksik; puan şartı doğrulanamadı'
    elif assessment.get('score_ambiguous'):
        score = f'KPSS: {kinds} | Başvuru eşiği: Kadroya göre değişiyor; eşleştirme doğrulanamadı'
    elif assessment.get('kpss_exempt'):
        score = 'KPSS şartı: Aranmadığı belirtiliyor'
    elif len(conditions) == 1:
        condition = conditions[0]
        value = f"{condition['minimum_score']:g}".replace('.', ',')
        score = f"KPSS: {condition['score_type']} | Başvuru eşiği: En az {value} puan"
    else:
        score = f'KPSS: {kinds} | Başvuru eşiği: Doğrulanamadı'
    degree = education or ', '.join(assessment.get('education_levels', []))
    degree = degree[:200] if degree else 'Doğrulanamadı; bölüm şartını ilandan kontrol edin'
    return [score, 'Mezuniyet / nitelik: ' + degree]


def message_for(item: dict, config: dict) -> str:
    assessment = item.get('assessment', {})
    priority = config['search_settings']['priority_city']
    city = f'📍 {priority} öncelikli\n' if normalize(priority) in normalize(item['title'] + ' ' + item['source']) else ''
    heading = '⏰ SON BAŞVURU HATIRLATMASI' if item.get('reminder') else '📢 İNCELENECEK İLAN'
    lines = [heading, city + 'Kaynak: ' + item['source'], item['title']]
    if assessment.get('deadline'):
        lines.append('Son başvuru: ' + assessment['deadline'])
    if item.get('detail_warning'):
        lines.append(item['detail_warning'])
    positions = assessment.get('positions', [])
    if positions:
        for position in positions[:8]:
            lines.append('\nKadro: ' + position.get('display_title', position['title']))
            lines.extend(requirement_lines(position['assessment'], position.get('display_education', position.get('education', ''))))
            codes = position.get('qualification_codes', [])
            if codes:
                lines.append('Nitelik kodu: ' + ', '.join(codes))
        if len(positions) > 8:
            lines.append(f'Diğer {len(positions) - 8} kadro için ilanı inceleyin.')
    else:
        lines.extend(requirement_lines(assessment))
    warnings = [w for w in assessment.get('warnings', [])
                if not w.startswith(('Puan eşiği karşılanıyor:', 'Aday puanı yaklaşık;',
                                     'KPSS taban puanı', 'Mezuniyet/bölüm uygunluğu'))]
    lines.extend('• ' + warning for warning in warnings[:3])
    candidate = config['candidate']
    score = str(candidate['kpss_score']).replace('.', ',')
    approximate = 'yaklaşık ' if candidate.get('score_is_approximate') else ''
    lines.extend(['', f"Takip profili: {candidate['kpss_score_type']} / {approximate}{score} (kişisel filtre).",
                  'Başvuru eşiği, yerleşme veya kazanma puanı değildir.',
                  'Bölüm, yaş, belge ve diğer özel şartlar resmî ilandan kontrol edilmeli.', item['url']])
    # Leave room for the official URL even when evidence is long.
    body = '\n'.join(lines[:-1])
    return body[:3500] + '\n' + item['url'][:500]


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


def report(status, **fields):
    record = {'status': status, **fields}
    print('RUN_SUMMARY ' + json.dumps(record, ensure_ascii=False))
    summary = os.getenv('GITHUB_STEP_SUMMARY') if os.getenv('KPSS_WRITE_SUMMARY') == '1' else None
    if summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            if 'sources' in fields:
                sources = fields['sources']
                readable = sum(s.get('pages_ok', 0) > 0 for s in sources)
                handle.write(f'## KPSS tarama sonucu: {status}\n\n')
                handle.write(f"- Okunabilen kaynak: **{readable}/{len(sources)}**\n")
                handle.write(f"- Telegram gönderimi: **{fields.get('messages_sent', 0)}**\n")
                handle.write(f"- Bekleyen gönderim: **{fields.get('pending_messages', 0)}**\n")
                handle.write(f"- Bekleyen ilan ayrıntısı: **{fields.get('pending_details', 0)}**\n\n")
                if status.endswith('partial_source_error'):
                    handle.write('⚠️ Bazı kaynaklar okunamadı; erişilebilen kaynakların taraması ve teslimat devam etti. Ayrıntılar aşağıdadır.\n\n')
            handle.write('```json\n' + json.dumps(record, ensure_ascii=False, indent=2) + '\n```\n')


def scan(config: dict, sources: dict, state_path: Path, dry_run: bool) -> int:
    zone = ZoneInfo(config.get('schedule', {}).get('timezone', 'Europe/Istanbul'))
    now = datetime.now(zone)
    start, end = int(config['schedule']['start_hour']), int(config['schedule']['end_hour'])
    in_window = start <= now.hour <= end if start <= end else now.hour >= start or now.hour <= end
    forced = os.getenv('FORCE_RUN') == '1'
    scheduled = os.getenv('KPSS_TRIGGER') == 'schedule'
    # Delayed scheduled runs still discover and persist notices. Quiet hours
    # defer delivery to the next daytime scan instead of losing the whole run.
    delivery_deferred = not dry_run and not forced and not in_window
    if delivery_deferred and not scheduled:
        report('skipped_time_window', local_time=now.isoformat(), timezone=str(zone))
        return 0
    if not dry_run and not config['notifications'].get('enabled', False):
        report('skipped_notifications_disabled')
        return 0
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    try:
        chat_ids = [] if dry_run else parse_chats(os.getenv('TELEGRAM_CHAT_IDS', ''))
    except ValueError:
        report('configuration_error', reason='Alıcı kimliklerinin biçimi geçersiz')
        return 1
    if not dry_run and (not token or not chat_ids):
        report('configuration_error', reason='Telegram secrets eksik')
        return 1
    # Validate state BEFORE external requests, and never replace corrupt history.
    state = get_state(state_path)
    notices = state.setdefault('notices', {})
    settings = config['notifications']
    failures, all_items, source_stats = 0, {}, []
    configured = [source for source in sources['sources'] if source.get('enabled', True)]
    def read_source(source):
        try:
            return source, scan_source(source), None
        except (OSError, ValueError) as exc:
            return source, [], type(exc).__name__
    with ThreadPoolExecutor(max_workers=4) as pool:
        for source, items, error in pool.map(read_source, configured):
            stats = dict(source.get('_scan_stats', {}), name=source['name'], candidates=len(items))
            if error:
                failures += 1
                stats['error'] = error
            source_stats.append(stats)
            print('SOURCE ' + json.dumps(stats, ensure_ascii=False))
            for item in items:
                item['url'] = canonical(item['url'])
                all_items[item['url']] = item
    if not configured or failures == len(configured):
        report('source_error', sources=source_stats, messages_sent=0)
        return 1
    policy = hashlib.sha256(('audit-v3-score-evidence:' + json.dumps(config, sort_keys=True)).encode()).hexdigest()[:16]
    priority = normalize(config['search_settings']['priority_city'])
    refresh = []
    for url, item in {**notices, **all_items}.items():
        cached = notices.get(url)
        # Prioritize unseen notices so a large daily refresh never starves new jobs.
        due = not cached or cached.get('checked_on') != now.date().isoformat() or cached.get('policy') != policy
        if due:
            refresh.append((cached is not None, priority not in normalize(item['title'] + ' ' + item['source']), url, item))
    refresh.sort(key=lambda item: item[:3])
    limit = max(1, min(int(config.get('processing', {}).get('max_details_per_run', 12)), 30))
    detail_failures = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item in pool.map(enrich, [entry[3] for entry in refresh[:limit]]):
            incomplete = bool(item.get('detail_warning'))
            detail_failures += int(incomplete)
            assessment = assess_notice(item.get('text', ''), config, now.date(), rows=item.get('rows'), incomplete=incomplete)
            item.pop('text', None)
            item.pop('rows', None)
            item.update(assessment=assessment, checked_on=now.date().isoformat(), policy=policy)
            notices[item['url']] = item
    documents, aliases = state.setdefault('documents', {}), state.setdefault('aliases', {})
    for url, item in notices.items():
        # Backward compatible: old URL receipts remain in sent, never deleted.
        identity = item.get('document_key')
        if identity:
            aliases[url] = documents.setdefault(identity, aliases.get(url, url))
    def anchor(url):
        return aliases.get(url, url)
    def receipts(key):
        root, suffix = key.split('#reminder:', 1) if '#reminder:' in key else (key, None)
        keys = {root, *(url for url in aliases if anchor(url) == root)}
        if suffix is not None:
            keys = {url + '#reminder:' + suffix for url in keys}
        return set(chat for k in keys for chat in state['sent'].get(k, {}).get('chats', []))
    queued, used = [], set()
    for url, item in sorted(notices.items(), key=lambda pair: (priority not in normalize(pair[1]['title'] + ' ' + pair[1]['source']), pair[0])):
        if item.get('policy') != policy:
            continue
        assessment = item['assessment']
        if assessment['status'] == 'excluded':
            continue
        deadline = assessment.get('deadline')
        if deadline and deadline < now.date().isoformat() and config['search_settings'].get('only_active_applications', True):
            continue
        root = anchor(url)
        if root in used:
            continue
        used.add(root)
        original = receipts(root)
        # Reminders require a CURRENT detail refresh, including notices no longer listed.
        if settings.get('notify_deadline_approaching', True) and deadline and item.get('checked_on') == now.date().isoformat() and not item.get('detail_warning'):
            days = (date.fromisoformat(deadline) - now.date()).days
            if days in settings.get('deadline_reminder_days', [3, 1, 0]) and original:
                key = root + '#reminder:' + deadline + ':' + str(days)
                queued.append((dict(item, reminder=True), key, original))
        if settings.get('notify_new_jobs', True):
            queued.append((item, root, None))
    # Count ALL recipient attempts; successes can never exceed this cap.
    cap = max(1, min(int(settings.get('max_notifications_per_run', 20)), 100))
    attempts, sent, errors, pending = 0, 0, 0, 0
    failed_chats = set()
    cursor = int(state.get('delivery_cursor', 0)) % max(1, len(chat_ids))
    ordered_chats = chat_ids[cursor:] + chat_ids[:cursor]
    for item, key, allowed in queued:
        completed = receipts(key)
        if dry_run:
            if not completed:
                print('[ÖN İZLEME] ' + message_for(item, config))
            continue
        for chat_id in ordered_chats:
            receipt = chat_fingerprint(token, chat_id)
            if receipt in completed or (allowed is not None and receipt not in allowed):
                continue
            if delivery_deferred or attempts >= cap or receipt in failed_chats:
                pending += 1
                continue
            attempts += 1
            try:
                telegram_request(token, 'sendMessage', {'chat_id': chat_id, 'text': message_for(item, config), 'disable_web_page_preview': True})
            except RuntimeError:
                errors += 1
                failed_chats.add(receipt)
                continue
            completed.add(receipt)
            state['sent'][key] = {'title': item['title'], 'chats': sorted(completed), 'at': now.isoformat()}
            sent += 1
            save_state(state_path, state)
    partial = failures or detail_failures or any(s.get('pages_failed', 0) for s in source_stats)
    status = 'telegram_error' if errors else 'partial_source_error' if partial else 'delivery_deferred' if delivery_deferred else 'success'
    if not dry_run:
        state['delivery_cursor'] = (cursor + attempts) % max(1, len(chat_ids))
        state['last_run'] = {'at': now.isoformat(), 'status': status,
                             'messages_sent': sent, 'delivery_deferred': delivery_deferred,
                             'sources_ok': sum(s.get('pages_ok', 0) > 0 for s in source_stats),
                             'sources_total': len(configured), 'details_failed': detail_failures,
                             'pending_messages': pending, 'pending_details': max(0, len(refresh)-limit)}
        save_state(state_path, state)
    report(('preview_' + status) if dry_run else status, local_time=now.isoformat(), timezone=str(zone),
           sources=source_stats, details_failed=detail_failures, messages_attempted=attempts,
           messages_sent=sent, pending_messages=pending, pending_details=max(0, len(refresh)-limit),
           dry_run=dry_run, delivery_deferred=delivery_deferred,
           recipients_configured=len(chat_ids))
    if partial:
        print('::warning::Bazı liste veya ilan belgeleri okunamadı; kapsam eksik.')
    return 1 if errors else 2 if partial and os.getenv('KPSS_STRICT_SOURCES') == '1' else 0


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
        report('program_error', error_type=type(exc).__name__)
        print('Program durduruldu; mevcut geçmiş korunuyor.')
        sys.exit(1)
