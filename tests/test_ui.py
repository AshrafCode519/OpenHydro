"""
Dashboard UI tests for OpenHydro
The real page in headless Edge/Chrome against the real server: it loads without errors, its styles
come from the built Tailwind file, rows update in place, settings save and the clipboard offer works.
The browser tests are skipped when no Chromium-based browser is installed.
"""

import os
import shutil
import tempfile
import time
import unittest

from starlette.testclient import TestClient

from browser import NO_ANIMATIONS, Browser, DashboardServer, find_browser, seed_tasks
from helpers import make_engine
from openhydro.api import create_app
from openhydro.core import TaskStatus
from openhydro.core.clipboard import ClipboardWatcher

BROWSER = find_browser()
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "openhydro", "web")
ROWS = "[...document.querySelectorAll('#tasks-container > [data-task-id]')]"
ROW_NAMES = f"{ROWS}.map(el => el.querySelector('h3').textContent)"


class FakeClipboard:
    def __init__(self):
        self.text, self.seq = None, 1

    def copy(self, text):
        self.text, self.seq = text, self.seq + 1

    def sequence(self):
        return self.seq

    def read_text(self):
        return self.text


def eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.05)


class TestDashboardServer(unittest.TestCase):
    """What the page is served with (no browser needed)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.api = TestClient(create_app(self.engine, remote_token="t"),
                              base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def test_page_uses_the_built_stylesheet(self):
        page = self.api.get("/").text
        self.assertIn('href="/static/css/tailwind.css"', page)
        self.assertNotIn("tailwindcss-3", page, "the in-page Tailwind compiler is gone")
        self.assertGreater(len(self.api.get("/static/css/tailwind.css").text), 10_000)

    def test_dashboard_files_are_always_revalidated(self):
        for path in ("/", "/static/js/app.js", "/static/css/tailwind.css"):
            self.assertEqual(self.api.get(path).headers.get("cache-control"), "no-cache", path)
        self.assertIsNone(self.api.get("/api/version").headers.get("cache-control"))

    def test_free_space_when_the_download_folder_is_missing(self):
        # Created by the first download, or on a drive that was unplugged: this used to be an HTTP 500
        self.engine.download_dir = os.path.join(self.dir, "not", "created", "yet")
        info = self.api.get("/api/system").json()
        self.assertGreater(info["disk_free_bytes"], 0)


@unittest.skipUnless(BROWSER, "no Edge/Chrome to run the dashboard in")
class DashboardTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = Browser(BROWSER)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.tasks = seed_tasks(self.engine, self.dir)
        self.clipboard = FakeClipboard()
        self.watcher = ClipboardWatcher(self.engine, clipboard=self.clipboard, interval=3600)
        self.watcher.start()
        self.addCleanup(self.watcher.stop)
        self.server = DashboardServer(self.engine, clipboard=self.watcher)
        self.addCleanup(self.server.stop)
        self.load()

    def load(self):
        self.browser.open(self.server.url)
        self.browser.evaluate(NO_ANIMATIONS)
        self.browser.wait_for(f"{ROWS}.length === 4", message="the downloads were not listed")

    def mark_rows(self):
        self.browser.evaluate(f"{ROWS}.forEach(el => {{ el.__kept = true; }})")

    def kept_rows(self):
        """For each row (in order): its name, and whether it is still the element marked earlier."""
        return self.browser.evaluate(f"{ROWS}.map(el => [el.querySelector('h3').textContent, !!el.__kept])")

    def assertNoJsErrors(self):
        self.assertEqual(self.browser.js_errors(), [])


class TestDashboardPage(DashboardTestCase):
    def test_loads_without_errors(self):
        self.assertEqual(self.browser.evaluate(ROW_NAMES), [
            "Ubuntu-24.04-desktop-amd64.iso", "Lecture 05 - Algorithms.mp4", "dataset.zip",
            "OpenHydro_Setup_v1.2.3.exe"], "downloading first, then newest first")
        self.browser.wait_for("document.getElementById('disk-free-display').innerText.includes('B')",
                              message="free space was not shown")
        self.assertNoJsErrors()

    def test_styles_come_from_the_built_stylesheet(self):
        styles = self.browser.evaluate("""({
          compiler: !!document.querySelector('script[src*="tailwind"]'),
          header: getComputedStyle(document.querySelector('header')).height,
          hiddenModal: getComputedStyle(document.getElementById('add-modal')).display,
          badge: getComputedStyle(document.querySelector('#tasks-container .rounded-full')).borderRadius,
        })""")
        self.assertEqual(styles, {"compiler": False, "header": "64px", "hiddenModal": "none",
                                  "badge": "9999px"})
        self.browser.evaluate("openAddModal()")
        self.assertEqual(self.browser.evaluate("getComputedStyle(document.getElementById('add-modal')).display"),
                         "flex")


class TestTaskList(DashboardTestCase):
    def test_progress_rebuilds_only_the_downloading_row(self):
        self.mark_rows()
        task = self.tasks["downloading"]
        task.chunks[2].current_byte = task.chunks[2].end_byte  # 66.67% -> 75%
        self.engine._notify("task_progress", task)
        self.browser.wait_for(f"{ROWS}[0].textContent.includes('75%')", message="progress was not shown")
        self.assertEqual(self.kept_rows(), [
            ["Ubuntu-24.04-desktop-amd64.iso", False], ["Lecture 05 - Algorithms.mp4", True],
            ["dataset.zip", True], ["OpenHydro_Setup_v1.2.3.exe", True]])
        self.assertEqual(self.browser.evaluate(f"{ROWS}[0].querySelectorAll('svg.lucide').length > 0"), True,
                         "icons of the rebuilt row")
        self.assertNoJsErrors()

    def test_rows_follow_status_changes_filters_and_removal(self):
        self.mark_rows()
        retried = self.tasks["error"]
        retried.status = TaskStatus.DOWNLOADING
        self.engine._notify("task_updated", retried)
        self.browser.wait_for(f"{ROW_NAMES}[1] === 'dataset.zip'", message="the resumed download did not move up")
        # Moved rows are the same elements; only the changed one was rebuilt
        self.assertEqual(self.kept_rows(), [
            ["Ubuntu-24.04-desktop-amd64.iso", True], ["dataset.zip", False],
            ["Lecture 05 - Algorithms.mp4", True], ["OpenHydro_Setup_v1.2.3.exe", True]])

        self.browser.evaluate("setCategoryFilter('completed')")
        self.assertEqual(self.browser.evaluate(ROW_NAMES), ["OpenHydro_Setup_v1.2.3.exe"])
        self.browser.evaluate("setCategoryFilter('paused')")
        self.assertEqual(self.browser.evaluate(f"{ROWS}.length"), 1)
        self.browser.evaluate("document.getElementById('search-input').value = 'nothing like this'; renderTasks()")
        self.assertIn("لا توجد تنزيلات", self.browser.evaluate("document.getElementById('tasks-container').textContent"))
        self.browser.evaluate("document.getElementById('search-input').value = ''; setCategoryFilter('all')")
        self.assertEqual(self.browser.evaluate(f"{ROWS}.length"), 4)

        self.engine._notify("task_cancelled", self.tasks["completed"])
        self.browser.wait_for(f"{ROWS}.length === 3", message="the deleted download stayed in the list")
        self.assertNoJsErrors()


class TestSettingsPage(DashboardTestCase):
    def test_changes_are_saved(self):
        self.browser.evaluate("openSettingsModal()")
        self.browser.wait_for("document.getElementById('set-download-dir').placeholder !== ''")
        self.browser.evaluate("""
          document.getElementById('set-notify').click();
          document.getElementById('set-default-connections').value = '16';
          document.querySelector('#settings-modal button[onclick="saveSettings()"]').click();
        """)
        eventually(lambda: self.engine.settings.get("default_connections") == 16)
        self.assertFalse(self.engine.settings.get("notify_on_complete"))
        self.browser.wait_for("document.getElementById('settings-modal').classList.contains('hidden')")
        # New downloads start with the saved default
        self.browser.evaluate("openAddModal()")
        self.assertEqual(self.browser.evaluate("document.getElementById('modal-connections-select').value"), "16")
        self.assertNoJsErrors()


class TestClipboardOffer(DashboardTestCase):
    def test_copied_link_is_offered_and_opens_the_add_dialog(self):
        self.clipboard.copy("https://example.com/tool.msi")
        self.watcher.check()
        self.browser.wait_for("!document.getElementById('clipboard-offer').classList.contains('hidden')",
                              message="the card did not appear")
        self.assertEqual(self.browser.evaluate("document.getElementById('clipboard-offer-name').textContent"),
                         "tool.msi")
        self.browser.evaluate("document.querySelector('#clipboard-offer button:last-child').click()")
        self.browser.wait_for("!document.getElementById('add-modal').classList.contains('hidden')")
        self.assertEqual(self.browser.evaluate("document.getElementById('modal-url-input').value"),
                         "https://example.com/tool.msi")
        eventually(lambda: self.watcher.current_offer() is None)

    def test_notification_click_before_the_window_loaded(self):
        # Started with Windows: the window is created by the click, so the page asks for the link itself
        self.browser.open("about:blank")
        self.clipboard.copy("https://example.com/movie.mkv")
        self.watcher.check()
        self.watcher.request_open()
        self.browser.open(self.server.url)
        self.browser.wait_for("!document.getElementById('add-modal').classList.contains('hidden')",
                              message="the add dialog did not open")
        self.assertEqual(self.browser.evaluate("document.getElementById('modal-url-input').value"),
                         "https://example.com/movie.mkv")
        self.assertTrue(self.browser.evaluate("document.getElementById('clipboard-offer').classList.contains('hidden')"))
        self.assertNoJsErrors()


if __name__ == "__main__":
    unittest.main()
