"""Bounded GET requests with robots.txt and per-origin pacing shared by all workers."""
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

AGENT = 'KPSS-Ilan-Takip/2.1'
_locks, _policies, _last = {}, {}, {}
_guard = threading.Lock()


def safe_error(exc):
    """Stable, useful diagnostics without URLs, response bodies or credentials."""
    if isinstance(exc, HTTPError):
        return 'http_' + str(exc.code)
    if isinstance(exc, PermissionError):
        message = str(exc).lower()
        if 'robots' in message:
            return 'robots_denied_or_unavailable'
        if 'doğrulama' in message:
            return 'automation_challenge'
        return 'access_denied'
    if isinstance(exc, TimeoutError):
        return 'timeout'
    if isinstance(exc, ValueError):
        return 'invalid_or_unsupported_content'
    return type(exc).__name__


def origin(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('Yalnız HTTPS kamu kaynağı desteklenir')
    return f'https://{p.netloc.lower()}'


def _raw(url, limit):
    request = Request(url, headers={'User-Agent': AGENT, 'Accept': 'text/html,application/pdf,application/rss+xml,application/atom+xml'})
    # Redirects re-enter fetch_bytes, so target robots and rate limits also apply.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with build_opener(NoRedirect).open(request, timeout=10) as response:
        mime = response.headers.get('Content-Type', '').lower()
        # Some sites route a missing robots.txt to their very large HTML homepage.
        # HTML is not a robots policy; read only a small prefix in that case.
        if urlsplit(url).path == '/robots.txt' and 'html' in mime:
            prefix = response.read(4096)
            if b'<html' in prefix.lower() or b'<!doctype html' in prefix.lower():
                return b'', response.headers, response.geturl()
            body = prefix + response.read(max(0, limit + 1 - len(prefix)))
        else:
            body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Belge boyutu sınırı aşıldı')
        return body, response.headers, response.geturl()


def fetch_bytes(url, limit=4_000_000, redirects=0):
    host = origin(url)
    with _guard:
        lock = _locks.setdefault(host, threading.RLock())
    with lock:
        if host not in _policies:
            robot = RobotFileParser()
            try:
                robots_url = host + '/robots.txt'
                for hop in range(5):
                    try:
                        body, _, _ = _raw(robots_url, 200_000)
                        break
                    except HTTPError as redirect:
                        if redirect.code not in (301, 302, 303, 307, 308) or hop == 4:
                            raise
                        from urllib.parse import urljoin
                        robots_url = urljoin(robots_url, redirect.headers.get('Location', ''))
                        origin(robots_url)
                robot.parse(body.decode('utf-8', errors='replace').splitlines())
            except HTTPError as exc:
                if exc.code in (404, 410):
                    robot.parse([])
                else:
                    robot.parse(['User-agent: *', 'Disallow: /'])
            except OSError:
                robot.parse(['User-agent: *', 'Disallow: /'])
            _policies[host] = robot
            _last[host] = time.monotonic()
        robot = _policies[host]
        if not robot.can_fetch(AGENT, url):
            raise PermissionError('robots.txt erişime izin vermiyor veya okunamadı')
        delay = max(1, robot.crawl_delay(AGENT) or 0)
        rate = robot.request_rate(AGENT)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 10:
            raise PermissionError('Kaynak daha uzun bekleme istiyor; bu taramada atlandı')
        pause = delay - (time.monotonic() - _last.get(host, 0))
        if pause > 0:
            time.sleep(pause)
        try:
            return _raw(url, limit)
        except HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308) or redirects >= 4:
                raise
            from urllib.parse import urljoin
            target = urljoin(url, exc.headers.get('Location', ''))
        finally:
            _last[host] = time.monotonic()
    return fetch_bytes(target, limit, redirects + 1)
