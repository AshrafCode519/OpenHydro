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
from unittest import mock

from starlette.testclient import TestClient

from browser import NO_ANIMATIONS, Browser, DashboardServer, find_browser, seed_tasks
from helpers import make_engine
from openhydro.api import create_app
from openhydro.core import TaskStatus, ffmpeg_component
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

    CONFIRM = """({
      shown: !document.getElementById('app-confirm-modal').classList.contains('hidden'),
      title: document.getElementById('confirm-modal-title').innerText,
      icon: document.querySelector('#confirm-modal-icon-bg svg').getAttribute('data-lucide'),
      red: document.getElementById('confirm-modal-ok').className.includes('from-rose-500'),
    })"""

    def test_clear_completed(self):
        self.browser.evaluate("document.querySelector('button[onclick=\"clearCompletedTasks()\"]').click()")
        self.assertEqual(self.browser.evaluate(self.CONFIRM),
                         {"shown": True, "title": "مسح المكتملة من القائمة", "icon": "list-x", "red": False})
        self.browser.evaluate("document.getElementById('confirm-modal-ok').click()")
        self.browser.wait_for(f"{ROWS}.length === 3", message="the completed download stayed in the list")
        self.assertNotIn(TaskStatus.COMPLETED, [t.status for t in self.engine.tasks.values()])
        self.assertNoJsErrors()

    def test_removing_a_download_still_asks_in_red(self):
        self.browser.evaluate(f"deleteTask({self.tasks['paused'].id!r})")
        self.assertEqual(self.browser.evaluate(self.CONFIRM),
                         {"shown": True, "title": "إزالة التنزيل", "icon": "trash-2", "red": True})

    def test_same_link_twice_asks_first(self):
        probe = {"filename": "Lecture 05 - Algorithms.mp4", "total_bytes": 2**20, "supports_ranges": True}
        with mock.patch.object(self.engine, "probe_url", mock.AsyncMock(return_value=probe)), \
                mock.patch.object(self.engine, "enqueue_or_start", mock.AsyncMock()):
            url = self.tasks["paused"].url
            self.browser.evaluate(f"openAddModal(); document.getElementById('modal-url-input').value = {url!r}; submitNewTask()")
            self.assertEqual(self.browser.evaluate(self.CONFIRM),
                             {"shown": True, "title": "الرابط ده موجود في القائمة", "icon": "copy", "red": False})
            self.assertIn("متوقف مؤقتاً", self.browser.evaluate("document.getElementById('confirm-modal-msg').innerText"))

            self.browser.evaluate("document.getElementById('confirm-modal-cancel').click()")
            time.sleep(0.5)
            self.assertEqual(len(self.engine.tasks), 4, "answering no adds nothing")

            self.browser.evaluate("submitNewTask()")
            self.browser.evaluate("document.getElementById('confirm-modal-ok').click()")
            eventually(lambda: len(self.engine.tasks) == 5)
        self.assertEqual(sum(t.url == url for t in self.engine.tasks.values()), 2)
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

    def test_ffmpeg_can_be_downloaded_from_the_settings(self):
        missing = {"supported": True, "available": False, "source": None, "version": None,
                   "installing": False, "progress": None}
        ready = {**missing, "available": True, "source": "openhydro", "version": "N-1-test"}
        status = mock.Mock(return_value=missing)

        def ensure(on_progress):
            on_progress(45, 90)
            status.return_value = ready
            return "ffmpeg.exe"

        with mock.patch.object(ffmpeg_component, "status", status), \
                mock.patch.object(ffmpeg_component, "ensure", side_effect=ensure) as ensure_mock:
            self.browser.evaluate("openSettingsModal()")
            self.browser.wait_for("!document.getElementById('set-ffmpeg-install').classList.contains('hidden')",
                                  message="no download button while ffmpeg is missing")
            self.browser.evaluate("document.getElementById('set-ffmpeg-install').click()")
            self.browser.wait_for("document.getElementById('set-ffmpeg-status').textContent.includes('N-1-test')",
                                  message="the finished download was not shown")
        ensure_mock.assert_called_once()
        self.assertTrue(self.browser.evaluate("document.getElementById('set-ffmpeg-install').classList.contains('hidden')"))
        self.assertNoJsErrors()


class TestPreparingFfmpeg(DashboardTestCase):
    def test_the_row_says_what_the_download_is_waiting_for(self):
        task = self.tasks["downloading"]
        task.phase = "تجهيز ffmpeg لأول مرة (دمج الصوت والصورة): 50% من 90 MB"
        self.engine._notify("task_updated", task)
        self.browser.wait_for(f"{ROWS}[0].textContent.includes('تجهيز ffmpeg')", message="the phase was not shown")
        task.phase = None
        self.engine._notify("task_updated", task)
        self.browser.wait_for(f"!{ROWS}[0].textContent.includes('تجهيز ffmpeg')")


class TestLayout(DashboardTestCase):
    """The app opens at 1100x720 (min 800x600) and the phone remote serves this same page."""

    SIZES = [(390, 844, True), (800, 600, False), (1100, 720, False), (1920, 1080, False)]

    def setUp(self):
        super().setUp()
        for _ in range(2):  # 12 downloads: more than a screen at every size
            seed_tasks(self.engine, self.dir)
        self.addCleanup(self.browser.send, "Emulation.clearDeviceMetricsOverride")

    def show(self, width, height, mobile):
        self.browser.send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                          deviceScaleFactor=1, mobile=mobile)
        self.browser.open(self.server.url)
        self.browser.wait_for(f"{ROWS}.length === 12")

    def test_fits_the_window_and_the_list_scrolls(self):
        for width, height, mobile in self.SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.show(width, height, mobile)
                layout = self.browser.evaluate("""({
                  pageWidth: document.documentElement.scrollWidth,
                  pageHeight: Math.round(document.body.getBoundingClientRect().height),
                  listScrolls: (l => l.scrollHeight > l.clientHeight)(document.getElementById('tasks-container')),
                })""")
                self.assertEqual(layout, {"pageWidth": width, "pageHeight": height, "listScrolls": True})

    def test_every_header_action_is_reachable(self):
        for width, height, mobile in self.SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.show(width, height, mobile)
                if width < 1024:
                    self.browser.evaluate("document.getElementById('header-menu-btn').click()")
                hidden = self.browser.evaluate("""
                  [...document.querySelectorAll('header button[onclick]:not([onclick^="setSpeedLimit"]), #limiter-btn')].filter(b => {
                    const r = b.getBoundingClientRect();
                    return !b.offsetParent && b.id !== 'header-menu-btn' || r.left < 0 || r.right > innerWidth;
                  }).map(b => b.title || b.textContent.trim())
                """)
                self.assertEqual(hidden, [])
                if width < 1024:
                    # Choosing an action closes the menu
                    self.browser.evaluate("document.querySelector('#header-actions button[onclick^=\"openSettingsModal\"]').click()")
                    self.assertTrue(self.browser.evaluate(
                        "document.getElementById('header-actions').classList.contains('hidden')"))

    def test_category_picker_replaces_the_sidebar_on_phones(self):
        self.show(390, 844, True)
        self.assertIsNone(self.browser.evaluate("document.querySelector('aside').offsetParent"))
        self.browser.evaluate("""
          const select = document.getElementById('category-select');
          select.value = 'completed';
          select.dispatchEvent(new Event('change'));
        """)
        self.assertEqual(self.browser.evaluate(f"{ROWS}.length"), 3)
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


class TestDownloadWindowPage(DashboardTestCase):
    """web/download.html: what the desktop app's download window shows (at its real size)."""

    def setUp(self):
        super().setUp()
        self.browser.send("Emulation.setDeviceMetricsOverride", width=484, height=341, deviceScaleFactor=1,
                          mobile=False)
        self.addCleanup(self.browser.send, "Emulation.clearDeviceMetricsOverride")

    def open_window(self, task):
        self.browser.open(f"{self.server.url}static/download.html?task={task.id}")
        self.browser.evaluate(NO_ANIMATIONS)
        self.browser.wait_for(f"document.getElementById('dw-name').textContent === {task.filename!r}",
                              message="the download was not shown")

    def text(self, element_id):
        return self.browser.evaluate(f"document.getElementById('{element_id}').textContent.trim()")

    def visible(self, element_id):
        return self.browser.evaluate(f"!!document.getElementById('{element_id}').offsetParent")

    def test_progress_pause_and_completion(self):
        task = self.tasks["downloading"]
        self.open_window(task)
        self.assertEqual(self.text("dw-status"), "جاري التنزيل (8 اتصالات)")
        self.assertEqual(self.text("dw-size"), "6 GB")
        self.assertEqual(self.text("dw-done"), "4 GB (66.67%)")
        self.assertEqual(self.text("dw-speed"), "12.50 MB/s")
        self.assertEqual(self.text("dw-resumable"), "نعم")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#dw-segments .segment-cell').length"), 4)
        self.assertEqual(self.browser.evaluate("document.title"), "66% Ubuntu-24.04-desktop-amd64.iso")
        self.assertEqual([self.visible(b) for b in ("dw-pause", "dw-resume", "dw-cancel", "dw-open")],
                         [True, False, True, False])
        # Everything fits the fixed-size window
        self.assertEqual(self.browser.evaluate(
            "document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth"),
            True)

        task.chunks[2].current_byte = task.chunks[2].end_byte
        self.engine._notify("task_progress", task)
        self.browser.wait_for("document.title.startsWith('75%')", message="progress was not shown")

        self.browser.evaluate("document.getElementById('dw-pause').click()")
        eventually(lambda: task.status == "paused")
        self.browser.wait_for("!!document.getElementById('dw-resume').offsetParent")
        self.assertEqual(self.text("dw-status"), "متوقف مؤقتاً")

        task.status = TaskStatus.COMPLETED
        self.engine._notify("task_completed", task)
        self.browser.wait_for("!!document.getElementById('dw-complete').offsetParent", message="no completed view")
        self.assertEqual([self.visible(b) for b in ("dw-pause", "dw-cancel", "dw-open", "dw-folder")],
                         [False, False, True, True])
        self.assertEqual(self.text("dw-close"), "إغلاق")
        self.assertNoJsErrors()

    def test_error_and_cancel(self):
        task = self.tasks["error"]
        self.open_window(task)
        self.assertEqual(self.text("dw-status"), "خطأ في التنزيل")
        self.assertEqual(self.text("dw-message"), "HTTP 403")
        self.browser.evaluate("window.confirm = () => true; document.getElementById('dw-cancel').click()")
        eventually(lambda: task.status == "cancelled")
        self.browser.wait_for("document.getElementById('dw-status').textContent === 'تم إلغاء التنزيل'")
        self.assertNoJsErrors()

    def test_unknown_download(self):
        self.browser.open(f"{self.server.url}static/download.html?task=no-such-task")
        self.browser.wait_for("document.getElementById('dw-status').textContent === 'تم إلغاء التنزيل'")


if __name__ == "__main__":
    unittest.main()
