"""
Browser cookies for video downloads
The browser extension shares a site's cookies before it sends a link from it, so a video that needs the
user signed in (private, age-restricted, members only, "confirm you're not a bot") downloads the way it
plays in the browser. Kept in memory only, never written to disk: yt-dlp reads them from a text stream.
"""

import io
import threading
import time
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

MAX_COOKIES = 3000            # every site together; the ones shared longest ago go first
MAX_COOKIES_PER_SHARE = 500   # far more than any site sets for one page
FORBIDDEN = ("\t", "\r", "\n")  # they would break a line of the Netscape cookie file


class Cookie(NamedTuple):
    name: str
    value: str
    domain: str      # lower case, without the leading dot
    host_only: bool  # only for that exact host, not its subdomains
    path: str
    secure: bool
    expires: int     # 0: until the browser closes (a session cookie)
    shared_at: float

    def applies_to(self, host: str) -> bool:
        return host == self.domain or (not self.host_only and host.endswith("." + self.domain))

    def netscape_line(self) -> str:
        domain = self.domain if self.host_only else "." + self.domain
        return "\t".join((domain, "FALSE" if self.host_only else "TRUE", self.path,
                          "TRUE" if self.secure else "FALSE", str(self.expires), self.name, self.value))


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def from_browser(data: Dict, now: float) -> Optional[Cookie]:
    """A cookie as chrome.cookies.getAll gives it, or None if it is not one we can pass on."""
    name, value, domain = data.get("name"), data.get("value"), data.get("domain")
    if not all(isinstance(x, str) for x in (name, value, domain)) or not name or not domain.strip("."):
        return None
    path = data.get("path") if isinstance(data.get("path"), str) and data.get("path") else "/"
    if any(c in s for s in (name, value, domain, path) for c in FORBIDDEN):
        return None
    expires = data.get("expirationDate")
    expires = int(expires) if isinstance(expires, (int, float)) and not isinstance(expires, bool) else 0
    if expires and expires <= now:
        return None
    host_only = data.get("hostOnly")
    if not isinstance(host_only, bool):
        host_only = not domain.startswith(".")
    return Cookie(name=name, value=value, domain=domain.strip(".").lower(), host_only=host_only, path=path,
                  secure=data.get("secure") is True, expires=expires, shared_at=now)


class BrowserCookies:
    def __init__(self):
        self._lock = threading.Lock()
        self._cookies: Dict[Tuple[str, bool, str, str], Cookie] = {}

    def remember(self, url: str, cookies: Iterable[Dict]) -> int:
        """
        The cookies the browser sends to `url`, as it has them now: they replace the ones kept for that
        address (a cookie the browser dropped, e.g. after signing out, is dropped here too). Cookies
        that would not be sent to `url` are ignored. Returns how many were kept.
        """
        host = _host(url)
        if not host:
            return 0
        now = time.time()
        fresh = [c for c in (from_browser(d, now) for d in list(cookies)[:MAX_COOKIES_PER_SHARE] if isinstance(d, dict))
                 if c is not None and c.applies_to(host)]
        with self._lock:
            self._cookies = {key: c for key, c in self._cookies.items() if not c.applies_to(host)}
            for c in fresh:
                self._cookies[(c.domain, c.host_only, c.path, c.name)] = c
            if len(self._cookies) > MAX_COOKIES:
                newest = sorted(self._cookies.items(), key=lambda item: item[1].shared_at)[-MAX_COOKIES:]
                self._cookies = dict(newest)
        return len(fresh)

    def for_url(self, url: str) -> List[Cookie]:
        host = _host(url)
        now = time.time()
        with self._lock:
            return [c for c in self._cookies.values()
                    if host and c.applies_to(host) and not (c.expires and c.expires <= now)]

    def has_cookies(self, url: str) -> bool:
        return bool(self.for_url(url))

    def cookie_file(self, url: str) -> Optional[io.StringIO]:
        """The site's cookies as a Netscape cookie file in memory (yt-dlp's `cookiefile`), or None."""
        cookies = self.for_url(url)
        if not cookies:
            return None
        lines = ["# Netscape HTTP Cookie File"] + [c.netscape_line() for c in cookies]
        return io.StringIO("\n".join(lines) + "\n")

    def clear(self):
        with self._lock:
            self._cookies = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._cookies)


# One for the whole app: the server fills it, the video extractor reads it
browser_cookies = BrowserCookies()
