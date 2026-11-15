"""
Web page links for OpenHydro (no internet: a local server)
A link to a web page used to be downloaded as a file of HTML. Now: a page with a video gives the video
(yt-dlp's generic extractor), a page without one is refused with a reason, and a file is still a file.
"""

import http.server
import shutil
import tempfile
import threading
import unittest

from starlette.testclient import TestClient

from helpers import make_engine
from openhydro.api import create_app
from openhydro.core import DownloadEngine
from openhydro.core.extractor import VideoExtractor

PAGES = {
    "/article": ("text/html; charset=utf-8", b"<html><body><h1>News</h1><p>No video here.</p></body></html>"),
    "/watch": ("text/html; charset=utf-8",
               b"<html><head><title>A clip</title></head><body><video src='/media/clip.mp4' controls></video></body></html>"),
    "/media/clip.mp4": ("video/mp4", b"\x00" * 4096),
    "/files/report": ("application/octet-stream", b"\x01" * 4096),
}


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._answer(body=True)

    def do_HEAD(self):
        self._answer(body=False)

    def _answer(self, body):
        content_type, data = PAGES.get(self.path.split("?")[0], ("text/plain", b"not found"))
        self.send_response(200 if self.path.split("?")[0] in PAGES else 404)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def log_message(self, *args):
        pass


class WebPageTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class TestWhatALinkIs(WebPageTestCase):
    async def test_a_page_without_a_video_is_a_web_page(self):
        info = await DownloadEngine.probe_url(f"{self.base}/article")
        self.assertTrue(info.get("is_web_page"))
        self.assertFalse(info.get("is_video_platform"))

    async def test_a_page_with_a_video_gives_the_video(self):
        info = await DownloadEngine.probe_url(f"{self.base}/watch")
        self.assertTrue(info.get("is_video_platform"), info)
        self.assertFalse(info.get("is_web_page"))

    async def test_a_file_is_still_a_file(self):
        info = await DownloadEngine.probe_url(f"{self.base}/files/report")
        self.assertEqual(info["total_bytes"], 4096)
        self.assertFalse(info.get("is_web_page") or info.get("is_video_platform"))


class TestSitesYtDlpKnows(unittest.TestCase):
    def test_a_site_with_its_own_extractor_is_a_video_site(self):
        # Matched against yt-dlp's 1800+ extractors' link patterns (no request is made)
        self.assertTrue(VideoExtractor.has_site_extractor("https://www.ted.com/talks/sir_ken_robinson_do_schools_kill_creativity"))
        self.assertTrue(VideoExtractor.has_site_extractor("https://soundcloud.com/forss/flickermood"))

    def test_other_links_are_not(self):
        for url in ("https://example.com/files/setup.zip", "https://example.com/some/page",
                    "https://cdn.example.com/video/clip.mp4"):
            self.assertFalse(VideoExtractor.has_site_extractor(url), url)


class TestRefusedPages(WebPageTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.events = []
        self.engine.register_listener(lambda event, data: self.events.append((event, data)))

    def test_adding_a_page_without_a_video_says_why(self):
        api = TestClient(create_app(self.engine, remote_token="t"), base_url="http://127.0.0.1:8765",
                         client=("127.0.0.1", 50000))
        resp = api.post("/api/tasks", json={"url": f"{self.base}/article"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("صفحة ويب", resp.json()["detail"])
        self.assertEqual(self.engine.tasks, {}, "no HTML file in the list")

    def test_a_page_sent_by_the_browser_extension_is_reported_in_the_app(self):
        # The extension was already told "sent" (it is added in the background): the app says what happened
        with TestClient(create_app(self.engine, remote_token="t"), base_url="http://127.0.0.1:8765",
                        client=("127.0.0.1", 50000)) as api:
            self.assertEqual(api.post("/api/remote/push", json={"url": f"{self.base}/article"}).status_code, 200)
            for _ in range(200):
                if any(event == "add_failed" for event, _ in self.events):
                    break
                threading.Event().wait(0.05)
        failed = [data for event, data in self.events if event == "add_failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("صفحة ويب", failed[0]["error"])


if __name__ == "__main__":
    unittest.main()
