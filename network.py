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
_policy_errors = {}


class SourceAccessError(PermissionError):
    def __init__(self, reason, http_status=None):
        super().__init__(reason)
        self.reason, self.http_status = reason, http_status


class SourceContentError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def error_info(exc):
    """Allowlisted diagnostics only; never log exception URLs or credentials."""
    result = {'type': type(exc).__name__}
    if isinstance(exc, (SourceAccessError, SourceContentError)):
        result['reason'] = exc.reason
    code = exc.code if isinstance(exc, HTTPError) else getattr(exc, 'http_status', None)
    if code is not None:
        result['http_status'] = code
    return result


def origin(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise SourceContentError('invalid_https_url')
    return f'https://{p.netloc.lower()}'


def _raw(url, limit, robots=False):
    policy_request = robots or urlsplit(url).path == '/robots.txt'
    # robots.txt is plain text; asking only for HTML/PDF caused real HTTP 406
    # responses at MEB and Memurlar.net despite a publicly readable policy.
    accept = 'text/plain,*/*;q=0.1' if policy_request else 'text/html,application/pdf,application/rss+xml,application/atom+xml,application/xml,text/xml,*/*;q=0.1'
    request = Request(url, headers={'User-Agent': AGENT, 'Accept': accept})
    # Redirects re-enter fetch_bytes, so target robots and rate limits also apply.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with build_opener(NoRedirect).open(request, timeout=10) as response:
        mime = response.headers.get('Content-Type', '').lower()
        # Some sites route a missing robots.txt to their very large HTML homepage.
        # HTML is not a robots policy; read only a small prefix in that case.
        if policy_request and 'html' in mime:
            prefix = response.read(4096)
            if b'<html' in prefix.lower() or b'<!doctype html' in prefix.lower():
                return b'', response.headers, response.geturl()
            body = prefix + response.read(max(0, limit + 1 - len(prefix)))
        else:
            body = response.read(limit + 1)
        if len(body) > limit:
            raise SourceContentError('document_too_large')
        return body, response.headers, response.geturl()


def _get_with_retry(url, limit, min_delay=1, robots=False):
    # GET only. Never retry a blocked URL or a Telegram sendMessage request.
    for attempt in range(3):
        try:
            return _raw(url, limit, robots=robots)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
            if retry_after:
                # A long/dated server backoff is left for the next workflow.
                if not retry_after.isdigit() or int(retry_after) > 10:
                    raise
            delay = max(min_delay, 2 ** attempt, int(retry_after or 0))
        except OSError:
            if attempt == 2:
                raise
            delay = max(min_delay, 2 ** attempt)
        time.sleep(delay)


def fetch_bytes(url, limit=4_000_000, redirects=0):
    host = origin(url)
    with _guard:
        lock = _locks.setdefault(host, threading.RLock())
    with lock:
        if host not in _policies:
            _policy_errors.pop(host, None)
            robot = RobotFileParser()
            try:
                robots_url = host + '/robots.txt'
                for hop in range(5):
                    try:
                        # Keep robots context after redirects to an HTML homepage.
                        body, _, _ = _get_with_retry(robots_url, 512_000, robots=True)
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
                    _policy_errors[host] = SourceAccessError('robots_http_error', exc.code)
            except (OSError, ValueError) as exc:
                robot.parse(['User-agent: *', 'Disallow: /'])
                _policy_errors[host] = SourceAccessError('robots_unreadable_' + error_info(exc).get('reason', type(exc).__name__))
            _policies[host] = robot
            _last[host] = time.monotonic()
        robot = _policies[host]
        if not robot.can_fetch(AGENT, url):
            raise _policy_errors.get(host) or SourceAccessError('robots_disallowed')
        delay = max(1, robot.crawl_delay(AGENT) or 0)
        rate = robot.request_rate(AGENT)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 10:
            raise SourceAccessError('crawl_delay_exceeds_run_budget')
        pause = delay - (time.monotonic() - _last.get(host, 0))
        if pause > 0:
            time.sleep(pause)
        try:
            return _get_with_retry(url, limit, min_delay=delay)
        except HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308) or redirects >= 4:
                raise
            from urllib.parse import urljoin
            target = urljoin(url, exc.headers.get('Location', ''))
        finally:
            _last[host] = time.monotonic()
    return fetch_bytes(target, limit, redirects + 1)
