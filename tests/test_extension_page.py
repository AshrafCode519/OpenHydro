"""
Browser extension's video button, in a real page (headless Edge/Chrome)
content.js runs on a page laid out like most video players: a click-catching layer over the video. The
button must be outside the player and actually receive the click. Skipped without a browser.
"""

import os
import shutil
import tempfile
import unittest
import urllib.parse
from unittest import mock

from starlette.testclient import TestClient

from browser import Browser, find_browser
from helpers import make_engine
from openhydro.api import create_app
from openhydro.core.engine import WebPageError

BROWSER = find_browser()
EXTENSION = os.path.join(os.path.dirname(__file__), "..", "openhydro", "extension")

# A player as most sites build it (video.js, JW Player...): the video in its own box (a stacking context),
# and next to that box a transparent layer, above it, that takes every click. Anything put inside the
# video's box, whatever its z-index, stays under that layer.
PLAYER_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body { margin: 0; height: 3000px; font-family: sans-serif; }
  #player { position: relative; width: 640px; height: 360px; margin: 140px 40px 0; overflow: hidden; }
  #video-box { position: relative; z-index: 1; width: 100%; height: 100%; }
  #video-box video { width: 100%; height: 100%; background: #222; }
  #click-layer { position: absolute; inset: 0; z-index: 10; }
</style></head><body>
  <div id="player">
    <div id="video-box"><video id="video" src="https://media.example.invalid/clip.mp4"></video></div>
    <div id="click-layer" onclick="window.__playerClicks = (window.__playerClicks || 0) + 1"></div>
  </div>
</body></html>"""

BUTTON = "document.querySelector('.openhydro-grab-btn')"


def read(name):
    with open(os.path.join(EXTENSION, name), encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(BROWSER, "no Edge/Chrome to run the page in")
class TestVideoButtonOnOtherSites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = Browser(BROWSER)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def load(self, page=PLAYER_PAGE, probe_answer=None, content_js=None):
        self.browser.open("data:text/html;charset=utf-8," + urllib.parse.quote(page))
        # What the extension gives content.js: its CSS, and messages to background.js (stubbed)
        self.browser.evaluate(f"""
          window.__messages = [];
          window.chrome = {{ runtime: {{ sendMessage: (message, reply) => {{
            window.__messages.push(message);
            if (reply) setTimeout(() => reply({probe_answer or '{}'}), 50);
          }} }} }};
          const style = document.createElement('style');
          style.textContent = {read('content.css')!r};
          document.head.append(style);
        """)
        self.browser.evaluate(content_js or read("content.js"))
        self.browser.wait_for(f"!!{BUTTON}", message="no button was added")
        self.browser.wait_for(f"getComputedStyle({BUTTON}).display !== 'none'")

    def geometry(self):
        return self.browser.evaluate(f"""(() => {{
          const button = {BUTTON}.getBoundingClientRect();
          const video = document.getElementById('video').getBoundingClientRect();
          const hit = document.elementFromPoint(button.left + button.width / 2, button.top + button.height / 2);
          return {{
            outsideThePlayer: !document.getElementById('player').contains({BUTTON}),
            aboveTheVideo: button.bottom <= video.top,
            clickReachesTheButton: !!hit && !!hit.closest('.openhydro-wrapper'),
            gapToVideo: Math.round(video.top - button.bottom),
          }};
        }})()""")

    def test_button_sits_outside_the_player_and_takes_the_click(self):
        self.load()
        g = self.geometry()
        self.assertTrue(g["outsideThePlayer"])
        self.assertTrue(g["aboveTheVideo"], g)
        self.assertTrue(g["clickReachesTheButton"], "the player's click layer took the click")

        self.browser.evaluate(f"{BUTTON}.click()")
        self.browser.wait_for("!!document.querySelector('.openhydro-quality-menu')", message="no menu opened")
        self.assertFalse(self.browser.evaluate("window.__playerClicks || 0"), "the click went to the player")

    def test_it_follows_the_video_when_the_page_scrolls(self):
        self.load()
        before = self.geometry()["gapToVideo"]
        self.browser.evaluate("window.scrollTo(0, 100)")
        self.browser.wait_for(f"""(() => {{
          const b = {BUTTON}.getBoundingClientRect(), v = document.getElementById('video').getBoundingClientRect();
          return Math.round(v.top - b.bottom) === {before};
        }})()""", message="the button stayed behind")

    def test_a_video_filling_the_window_still_gets_a_clickable_button(self):
        page = PLAYER_PAGE.replace("width: 640px; height: 360px; margin: 140px 40px 0;",
                                   "width: 100vw; height: 100vh; margin: 0;")
        assert page != PLAYER_PAGE
        self.load(page)
        g = self.geometry()
        self.assertFalse(g["aboveTheVideo"], "no room above or under it: inside its top edge")
        self.assertTrue(g["clickReachesTheButton"])

    def test_menu_says_so_when_the_page_has_no_video_it_can_read(self):
        self.load(probe_answer='{is_web_page: true}')
        self.browser.evaluate(f"{BUTTON}.click()")
        self.browser.wait_for("(document.querySelector('.openhydro-quality-menu') || {}).textContent"
                              "?.includes('مفيش فيديو')", message="no explanation in the menu")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('.openhydro-quality-item')"),
                         "no download choices that cannot work")


class TestExtensionAddDialogAndWebPageRejection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.engine = make_engine(self, self.temp_dir)
        self.notifications = []
        self.engine.register_listener(lambda t, d: self.notifications.append((t, d)))
        self.app = create_app(self.engine, remote_token="secret-token")
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def test_add_dialog_flow(self):
        # 1. Post from extension send button
        res = self.client.post("/api/app/add_dialog", json={"url": "https://www.youtube.com/watch?v=sample"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "shown"})
        self.assertTrue(any(ev[0] == "add_dialog" for ev in self.notifications))

        # 2. OpenHydro dashboard queries active dialog link
        res_get = self.client.get("/api/app/add_dialog")
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.json()["link"]["url"], "https://www.youtube.com/watch?v=sample")

        # 3. Modal opens and clears the pending dialog
        res_del = self.client.delete("/api/app/add_dialog")
        self.assertEqual(res_del.status_code, 200)
        self.assertEqual(res_del.json(), {"status": "done"})

        res_get2 = self.client.get("/api/app/add_dialog")
        self.assertIsNone(res_get2.json()["link"])

    def test_add_dialog_rejects_external_hosts(self):
        remote_client = TestClient(self.app, base_url="http://192.168.1.50:8765", client=("192.168.1.50", 50000))
        res = remote_client.post("/api/app/add_dialog", json={"url": "https://example.com"})
        self.assertEqual(res.status_code, 403)

    def test_task_add_rejects_plain_webpage_with_400(self):
        # Mock probe_url to return is_web_page: True
        with mock.patch.object(self.engine, "probe_url", return_value={"is_web_page": True, "filename": "page.html", "total_bytes": 0}):
            res = self.client.post("/api/tasks", json={"url": "https://example.com/no-video-here"})
            self.assertEqual(res.status_code, 400)
            self.assertIn("صفحة ويب", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
