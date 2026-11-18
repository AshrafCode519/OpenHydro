"""
Browser cookies for videos that need the user signed in (private, age-restricted, members only)
The browser extension shares the site's cookies before it sends a link; OpenHydro keeps them in memory
and gives them to yt-dlp only when the video does not download without them.
"""

import asyncio
import http.server
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

from browser import Browser, DashboardServer, find_browser
from helpers import make_engine
from openhydro.api import create_app
from openhydro.core import DownloadEngine, browser_cookies as cookies_module
from openhydro.core.browser_cookies import browser_cookies
from openhydro.core.extractor import VideoDownloadError, VideoExtractor
from openhydro.core.hls import HLSDownloader

PAGE = "https://www.example.com/watch/1"
SIGN_IN_ERROR = ("ERROR: [example] 1: This video is only available for registered users. "
                 "Use --cookies-from-browser or --cookies for the authentication.")
READ_TIMEOUT = "ERROR: [download] Got error: HTTPSConnectionPool(host='cdn.example.com'): Read timed out."


def chrome_cookie(name, value, domain, host_only=False, **fields):
    """As chrome.cookies.getAll gives it."""
    return {"name": name, "value": value, "domain": domain, "path": "/", "secure": True, "hostOnly": host_only,
            "httpOnly": True, "sameSite": "lax", "session": "expirationDate" not in fields, "storeId": "0",
            **fields}


SIGNED_IN = [chrome_cookie("session", "abc", ".example.com"),
             chrome_cookie("pref", "1", "www.example.com", host_only=True, expirationDate=time.time() + 3600)]


class CookiesTestCase(unittest.TestCase):
    def setUp(self):
        browser_cookies.clear()
        self.addCleanup(browser_cookies.clear)


class TestCookieJar(CookiesTestCase):
    def test_only_cookies_the_browser_would_send_there(self):
        kept = browser_cookies.remember(PAGE, SIGNED_IN + [
            chrome_cookie("tracker", "x", ".other.com"),                                  # another site
            chrome_cookie("old", "x", ".example.com", expirationDate=time.time() - 60),  # expired
            chrome_cookie("bad", "a\tb", ".example.com"),                                # breaks the file
        ])
        self.assertEqual(kept, 2)
        names = lambda url: sorted(c.name for c in browser_cookies.for_url(url))  # noqa: E731
        self.assertEqual(names(PAGE), ["pref", "session"])
        self.assertEqual(names("https://cdn.example.com/v.mp4"), ["session"], "host-only stays on its host")
        self.assertEqual(names("https://other.com/"), [])

    def test_signing_out_in_the_browser_signs_out_here(self):
        browser_cookies.remember(PAGE, SIGNED_IN)
        browser_cookies.remember(PAGE, [])  # shared again after signing out: none left
        self.assertFalse(browser_cookies.has_cookies(PAGE))

    def test_yt_dlp_reads_them_from_memory(self):
        import yt_dlp

        browser_cookies.remember(PAGE, SIGNED_IN)
        stream = browser_cookies.cookie_file(PAGE)
        self.assertIsInstance(stream, io.StringIO, "never a file on disk")
        with yt_dlp.YoutubeDL({"cookiefile": stream, "quiet": True}) as ydl:
            self.assertEqual(sorted(ydl.cookiejar.get_cookie_header(PAGE).split("; ")), ["pref=1", "session=abc"])
            self.assertEqual(ydl.cookiejar.get_cookie_header("https://cdn.example.com/v.mp4"), "session=abc")
            self.assertFalse(ydl.cookiejar.get_cookie_header("http://www.example.com/"), "secure: HTTPS only")
        self.assertIsNone(browser_cookies.cookie_file("https://other.com/"))

    def test_memory_is_bounded(self):
        with mock.patch.object(cookies_module, "MAX_COOKIES", 3):
            browser_cookies.remember("https://a.example.org/", [chrome_cookie(n, "1", "a.example.org") for n in "xy"])
            browser_cookies.remember("https://b.example.org/", [chrome_cookie(n, "1", "b.example.org") for n in "xy"])
        self.assertEqual(len(browser_cookies), 3)
        self.assertEqual(len(browser_cookies.for_url("https://b.example.org/")), 2, "the latest site's are kept")


class SignInYoutubeDL:
    """A video that plays only signed in: yt-dlp fails without the site's session cookie."""
    calls = []
    needs_sign_in = True
    failure = SIGN_IN_ERROR

    def __init__(self, opts):
        self.opts = opts
        stream = opts.get("cookiefile")
        self.signed_in = stream is not None and "\tsession\tabc" in stream.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _attempt(self, what):
        SignInYoutubeDL.calls.append((what, self.signed_in))
        if SignInYoutubeDL.needs_sign_in and not self.signed_in:
            import yt_dlp
            raise yt_dlp.utils.DownloadError(SignInYoutubeDL.failure)

    def _write(self):
        with open(self.opts["outtmpl"][: -len(".%(ext)s")] + ".mp4", "wb") as f:
            f.write(b"media")

    def extract_info(self, url, download=False):
        self._attempt("extract")
        return {"id": "1", "title": "Members clip", "formats": [{"format_id": "hd", "height": 720}]}

    @staticmethod
    def sanitize_info(info, remove_private_keys=False):
        return dict(info)

    def process_ie_result(self, info, download=True):
        self._attempt("replay")
        self._write()

    def download(self, urls):
        self._attempt("download")
        self._write()


class TestSignedInVideos(CookiesTestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        VideoExtractor._info_cache = {}
        self.addCleanup(setattr, VideoExtractor, "_info_cache", {})
        SignInYoutubeDL.calls, SignInYoutubeDL.needs_sign_in, SignInYoutubeDL.failure = [], True, SIGN_IN_ERROR
        for patcher in (mock.patch("yt_dlp.YoutubeDL", SignInYoutubeDL),
                        mock.patch.object(HLSDownloader, "find_ffmpeg", return_value="ffmpeg.exe")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self):
        return VideoExtractor._sync_download(PAGE, os.path.join(self.dir, "Clip.mp4"), "best", 8, None, None)

    def test_without_the_browsers_cookies_the_reason_says_to_sign_in(self):
        with self.assertRaises(Exception) as caught:
            VideoExtractor._sync_extract(PAGE)
        self.assertIn("مسجّل دخول", VideoDownloadError(str(caught.exception)).message)
        self.assertEqual(SignInYoutubeDL.calls, [("extract", False)])

    def test_with_the_browsers_cookies_it_downloads(self):
        browser_cookies.remember(PAGE, SIGNED_IN)
        self.assertEqual(VideoExtractor._sync_extract(PAGE)["title"], "Members clip")  # the quality menu
        self.assertEqual(self.download(), os.path.join(self.dir, "Clip.mp4"))
        # Found only signed in: the download is signed in from the start, without a failing visitor try
        self.assertEqual(SignInYoutubeDL.calls, [("extract", False), ("extract", True), ("replay", True)])

    def test_a_download_on_its_own_signs_in_too(self):
        # e.g. a playlist's video, or one resumed after a restart: nothing extracted before
        browser_cookies.remember(PAGE, SIGNED_IN)
        self.assertIsNotNone(self.download())
        self.assertEqual(SignInYoutubeDL.calls, [("download", False), ("download", True)])

    def test_a_video_anyone_can_watch_never_gets_them(self):
        SignInYoutubeDL.needs_sign_in = False
        browser_cookies.remember(PAGE, SIGNED_IN)
        VideoExtractor._sync_extract(PAGE)
        self.assertIsNotNone(self.download())
        self.assertEqual(SignInYoutubeDL.calls, [("extract", False), ("replay", False)])

    def test_a_lost_connection_is_not_a_reason_to_sign_in(self):
        SignInYoutubeDL.failure = READ_TIMEOUT
        browser_cookies.remember(PAGE, SIGNED_IN)
        with self.assertRaises(VideoDownloadError) as caught:
            self.download()
        self.assertTrue(caught.exception.transient)  # the engine tries again later, as before
        self.assertEqual(SignInYoutubeDL.calls, [("download", False)])


MEMBERS_VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 8192


class _MembersSite(http.server.BaseHTTPRequestHandler):
    """A site whose video is only there for a signed-in visitor (the session cookie)."""

    def do_GET(self):
        signed_in = "session=abc" in (self.headers.get("Cookie") or "")
        if self.path == "/members/clip":
            body = (b"<html><head><title>Members clip</title></head><body><video src='/media/clip.mp4'></video>"
                    if signed_in else b"<html><head><title>Log in</title></head><body><form>Log in</form>")
            self._send(200, "text/html; charset=utf-8", body + b"</body></html>")
        elif self.path == "/media/clip.mp4" and signed_in:
            self._send(200, "video/mp4", MEMBERS_VIDEO)
        else:
            self._send(403, "text/plain", b"members only")

    def do_HEAD(self):
        self._send(200, "text/html; charset=utf-8", b"", length=0)

    def _send(self, status, content_type, body, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestRealYtDlpSignsIn(CookiesTestCase):
    """The whole way with the real yt-dlp (its generic extractor) and a local site: no fakes, no internet."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MembersSite)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.page = f"http://127.0.0.1:{cls.server.server_address[1]}/members/clip"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        VideoExtractor._info_cache = {}
        self.addCleanup(setattr, VideoExtractor, "_info_cache", {})

    def test_a_members_only_video_is_found_and_downloaded(self):
        # As a visitor the page is a login form: a web page, nothing to download
        self.assertTrue(asyncio.run(DownloadEngine.probe_url(self.page)).get("is_web_page"))

        browser_cookies.remember(self.page, [chrome_cookie("session", "abc", "127.0.0.1", host_only=True,
                                                           secure=False)])
        info = asyncio.run(DownloadEngine.probe_url(self.page))
        self.assertTrue(info.get("is_video_platform"), info)
        self.assertTrue(info["title"].startswith("Members clip"), info["title"])  # "... (1)": the page's first video

        path = VideoExtractor._sync_download(self.page, os.path.join(self.dir, "Members clip.mp4"), "best", 8,
                                             None, None)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), MEMBERS_VIDEO, "the video itself was fetched signed in too")


class TestCookiesEndpoint(CookiesTestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.engine.settings.update({"remote_access": True})
        self.app = create_app(self.engine, remote_token="t")
        self.local = TestClient(self.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def share(self, client=None, **headers):
        return (client or self.local).post("/api/browser/cookies", json={"url": PAGE, "cookies": SIGNED_IN},
                                           headers=headers)

    def test_the_extension_shares_them(self):
        resp = self.share(Origin="chrome-extension://abcdefghijklmnop")
        self.assertEqual(resp.json(), {"kept": 2, "enabled": True})
        self.assertTrue(browser_cookies.has_cookies(PAGE))

    def test_from_this_pc_only(self):
        phone = TestClient(self.app, base_url="http://192.168.1.5:8765", client=("192.168.1.20", 50000))
        self.assertEqual(self.share(phone, **{"X-OpenHydro-Token": "t"}).status_code, 403)
        self.assertEqual(self.share(Origin="https://evil.example").status_code, 403, "no website either")
        self.assertEqual(len(browser_cookies), 0)

    def test_nothing_reads_them_back(self):
        self.share()
        self.assertEqual(self.local.get("/api/browser/cookies").status_code, 405)
        self.assertNotIn("abc", self.local.get("/api/settings").text)

    def test_the_setting_turns_it_off(self):
        self.share()
        self.assertEqual(self.local.put("/api/settings", json={"use_browser_cookies": False}).status_code, 200)
        self.assertEqual(len(browser_cookies), 0, "what was shared is dropped at once")
        self.assertEqual(self.share().json(), {"kept": 0, "enabled": False})
        self.assertEqual(len(browser_cookies), 0)


# The extension's background.js in a page of the real app (so its requests reach the server), with the
# browser's chrome.* API stubbed: the site has a session cookie
EXTENSION_STUBS = """
  window.__listeners = {};
  const event = (name) => ({ addListener: (fn) => { window.__listeners[name] = fn; } });
  const realFetch = window.fetch.bind(window);
  window.fetch = (url, options) => realFetch(String(url).replace('http://127.0.0.1:8765', location.origin), options);
  window.chrome = {
    runtime: { onInstalled: event('installed'), onMessage: event('message') },
    contextMenus: { create() {}, onClicked: event('menu') },
    downloads: { onDeterminingFilename: event('download') },
    notifications: { create() {} },
    cookies: { getAll: async ({ url }) => new URL(url).hostname.endsWith('example.com') ? %s : [] },
  };
  window.send = (message, sender) => new Promise((reply) => window.__listeners.message(message, sender || {}, reply));
"""


@unittest.skipUnless(find_browser(), "no Edge/Chrome to run the extension's script in")
class TestExtensionSharesCookies(CookiesTestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = Browser(find_browser())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.server = DashboardServer(self.engine)
        self.addCleanup(self.server.stop)
        self.browser.open(self.server.url + "api/version")
        import json
        self.browser.evaluate(EXTENSION_STUBS % json.dumps(SIGNED_IN))
        with open(os.path.join(os.path.dirname(__file__), "..", "openhydro", "extension", "background.js"),
                  encoding="utf-8") as f:
            self.browser.evaluate(f.read())

    def test_before_the_quality_menu_is_asked_for(self):
        seen = []

        async def probe(url, custom_headers=None):
            seen.append(browser_cookies.has_cookies(url))
            return {"is_video_platform": True, "qualities": [{"label": "720p"}]}

        with mock.patch.object(self.engine, "probe_url", probe):
            answer = self.browser.evaluate(
                f"send({{ action: 'probe_url', url: '{PAGE}' }}, {{ url: '{PAGE}', tab: {{ url: '{PAGE}' }} }})")
        self.assertEqual(answer["qualities"], [{"label": "720p"}])
        self.assertEqual(seen, [True], "the cookies were there when the video was looked at")

    def test_before_a_video_is_sent(self):
        seen = []

        async def add_download(url, **options):
            seen.append(browser_cookies.has_cookies(url))
            raise ValueError("not downloaded in this test")

        with mock.patch.object(self.engine, "add_download", add_download):
            answer = self.browser.evaluate(
                f"send({{ action: 'download_media', url: '{PAGE}', referrer: '{PAGE}', format_id: 'best' }})")
            deadline = time.monotonic() + 5
            while not seen and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual(answer, {"success": True})
        self.assertEqual(seen, [True])

    def test_before_the_popup_opens_the_add_dialog(self):
        answer = self.browser.evaluate(f"send({{ action: 'open_add_dialog', url: '{PAGE}', page: '{PAGE}' }})")
        self.assertEqual(answer, {"success": True})
        self.assertTrue(browser_cookies.has_cookies(PAGE))
        self.assertEqual(self.browser.evaluate("fetch('/api/app/add_dialog').then((r) => r.json())"),
                         {"link": {"url": PAGE}})

    def test_a_site_without_cookies_clears_what_was_kept(self):
        browser_cookies.remember("https://videos.other.org/1", [chrome_cookie("old", "1", ".other.org")])
        self.browser.evaluate("send({ action: 'open_add_dialog', url: 'https://videos.other.org/1' })")
        self.assertFalse(browser_cookies.has_cookies("https://videos.other.org/1"), "signed out in the browser")


if __name__ == "__main__":
    unittest.main()
