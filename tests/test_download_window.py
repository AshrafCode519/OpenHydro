"""
Download window tests for OpenHydro (IDM-style progress window)
Which downloads open one, one window per download, and the calls the page makes into the window.
"""

import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from starlette.testclient import TestClient

from helpers import make_engine
from openhydro.api import create_app
from openhydro.core import download_windows
from openhydro.core.download_windows import DownloadWindows


def eventually(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


class FakeWindow:
    """The parts of a pywebview Window the download windows use."""

    def __init__(self, **options):
        self.options = options
        self.title = options["title"]
        self.on_top = options["on_top"]
        self.events = SimpleNamespace(closed=FakeEvent())
        self.shown = 0

    def show(self):
        self.shown += 1

    def restore(self):
        pass

    def destroy(self):
        self.events.closed.fire()

    def set_title(self, title):
        self.title = title


class TestDownloadWindows(unittest.TestCase):
    def setUp(self):
        self.created = []

        def create_window(**options):
            window = FakeWindow(**options)
            self.created.append(window)
            return window

        self.windows = DownloadWindows("http://127.0.0.1:8765/", create_window=create_window)
        patcher = mock.patch.object(download_windows, "ON_TOP_SECONDS", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_window_per_download(self):
        self.windows.open("task-1", "setup.exe")
        eventually(lambda: len(self.created) == 1)
        window = self.created[0]
        self.assertEqual(window.options["url"], "http://127.0.0.1:8765/static/download.html?task=task-1")
        self.assertEqual(window.title, "setup.exe")
        self.assertFalse(window.options["resizable"])

        self.windows.open("task-1", "setup.exe")  # e.g. the same download added again
        self.assertEqual(len(self.created), 1)
        self.assertEqual(window.shown, 1, "the existing window is brought forward")

        self.windows.open("task-2", "movie.mkv")
        eventually(lambda: len(self.created) == 2)

    def test_in_front_of_the_browser_then_a_normal_window(self):
        self.windows.open("task-1", "setup.exe")
        eventually(lambda: len(self.created) == 1)
        self.assertTrue(self.created[0].options["on_top"])
        eventually(lambda: self.created[0].on_top is False)

    def test_the_page_can_set_the_title_and_close_it(self):
        self.windows.open("task-1", "setup.exe")
        eventually(lambda: self.windows.is_open("task-1") and len(self.created) == 1)
        api = self.created[0].options["js_api"]
        api.set_title("45% setup.exe")
        self.assertEqual(self.created[0].title, "45% setup.exe")
        api.close()
        self.assertFalse(self.windows.is_open("task-1"))
        self.windows.open("task-1", "setup.exe")
        eventually(lambda: len(self.created) == 2)

    def test_a_failed_window_can_be_opened_again(self):
        failing = DownloadWindows("http://127.0.0.1:8765", create_window=mock.Mock(side_effect=RuntimeError("no GUI")))
        failing.open("task-1", "a.zip")
        eventually(lambda: not failing.is_open("task-1"))


class TestWhichDownloadsOpenAWindow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.engine.settings.update({"remote_access": True})
        probe = {"filename": "file.bin", "total_bytes": 4096, "supports_ranges": True}
        patcher = mock.patch.object(self.engine, "probe_url", mock.AsyncMock(return_value=probe))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Nothing is really downloaded
        patcher = mock.patch.object(self.engine, "enqueue_or_start", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.started = []
        self.app = create_app(self.engine, remote_token="t", on_download_started=self.started.append)

    def client(self, ip="127.0.0.1", host="127.0.0.1"):
        return TestClient(self.app, base_url=f"http://{host}:8765", client=(ip, 50000))

    def test_started_on_this_pc(self):
        task = self.client().post("/api/tasks", json={"url": "https://example.com/file.bin"}).json()
        self.assertEqual([t.id for t in self.started], [task["id"]])

    def test_not_for_later_downloads_the_phone_or_playlists(self):
        self.client().post("/api/tasks", json={"url": "https://example.com/a.bin", "auto_start": False})
        phone = self.client(ip="192.168.1.20", host="192.168.1.5")
        self.assertEqual(phone.post("/api/tasks?token=t", json={"url": "https://example.com/b.bin"}).status_code, 200)
        self.client().post("/api/playlist/download", json={"playlist_title": "List", "items": [
            {"url": "https://example.com/c.bin", "title": "c", "index": 1}]})
        self.assertEqual(self.started, [])

    def test_setting_is_on_by_default(self):
        self.assertTrue(self.engine.settings.get("show_download_window"))


if __name__ == "__main__":
    unittest.main()
