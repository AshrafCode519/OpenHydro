"""
Clipboard monitoring tests for OpenHydro
Which copied text counts as a download link, when it is offered, the API and the clickable notification.
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

from starlette.testclient import TestClient

from helpers import make_engine
from openhydro.core import DownloadTask
from openhydro.core.clipboard import ClipboardWatcher, WindowsClipboard, extract_download_link, link_filename
from openhydro.api import create_app


class TestLinkDetection(unittest.TestCase):
    def test_file_links(self):
        self.assertEqual(extract_download_link("https://example.com/files/setup.exe"), "https://example.com/files/setup.exe")
        signed = "https://cdn.example.com/a/Movie%20Name.MKV?token=abc&expires=1"
        self.assertEqual(extract_download_link(f"  {signed}\r\n"), signed)
        self.assertEqual(link_filename(signed), "Movie Name.MKV")

    def test_magnet_links(self):
        magnet = "magnet:?xt=urn:btih:0123456789abcdef&dn=Ubuntu%2024.04"
        self.assertEqual(extract_download_link(magnet), magnet)
        self.assertEqual(link_filename(magnet), "Ubuntu 24.04")
        self.assertIsNone(extract_download_link("magnet:?dn=no-hash"))

    def test_everything_else_is_ignored(self):
        for text in (None, "", "https://example.com/page.html", "https://www.youtube.com/watch?v=abc",
                     "https://example.com/", "download this: https://example.com/a.zip",
                     "https://example.com/a.zip\nhttps://example.com/b.zip", "ftp://example.com/a.zip",
                     "file:///C:/Users/me/a.zip", "C:\\Users\\me\\a.zip", "http://[::1/a.zip",
                     "https://example.com/" + "a" * 5000 + ".zip"):
            self.assertIsNone(extract_download_link(text), text)


class FakeClipboard:
    def __init__(self, text=None):
        self.text, self.seq, self.reads = text, 1, 0

    def copy(self, text):
        self.text, self.seq = text, self.seq + 1

    def sequence(self):
        return self.seq

    def read_text(self):
        self.reads += 1
        return self.text


class WatcherTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.events = []
        self.engine.register_listener(lambda event, data: self.events.append((event, dict(data))))
        self.offers = []
        self.clipboard = FakeClipboard("https://example.com/copied-before-start.zip")
        # A long interval: the tests drive the polling with check()
        self.watcher = ClipboardWatcher(self.engine, on_offer=self.offers.append, clipboard=self.clipboard,
                                        interval=3600)
        self.assertTrue(self.watcher.start())
        self.addCleanup(self.watcher.stop)

    def copy(self, text):
        self.clipboard.copy(text)
        self.watcher.check()


class TestClipboardWatcher(WatcherTestCase):
    def test_link_copied_before_start_is_not_offered(self):
        self.watcher.check()
        self.assertEqual(self.offers, [])

    def test_new_link_is_offered_once(self):
        self.copy("https://example.com/tool.msi")
        self.copy("https://example.com/tool.msi")
        self.watcher.check()
        self.assertEqual(self.offers, [{"url": "https://example.com/tool.msi", "filename": "tool.msi"}])
        self.assertEqual(self.events, [("clipboard_link", self.offers[0])])
        self.assertEqual(self.watcher.current_offer()["url"], "https://example.com/tool.msi")

        self.copy("just some text")
        self.assertEqual(len(self.offers), 1)
        self.copy("https://example.com/other.zip")
        self.assertEqual(self.watcher.current_offer()["url"], "https://example.com/other.zip", "the latest wins")

    def test_turned_off_means_not_read(self):
        self.engine.settings.update({"clipboard_monitor": False})
        self.copy("https://example.com/while-off.zip")
        self.assertEqual(self.clipboard.reads, 0)

        self.engine.settings.update({"clipboard_monitor": True})
        self.watcher.check()
        self.assertEqual(self.offers, [], "a copy made while it was off is not offered later")

    def test_links_already_in_the_list_are_not_offered(self):
        url = "https://example.com/known.iso"
        task = DownloadTask(url=url, filename="known.iso", save_path=os.path.join(self.dir, "known.iso"))
        self.engine.tasks[task.id] = task
        self.copy(url)
        self.assertEqual(self.offers, [])

    def test_offer_goes_away_once_added_another_way(self):
        url = "https://example.com/captured.zip"
        self.copy(url)
        task = DownloadTask(url=url, filename="captured.zip", save_path=os.path.join(self.dir, "captured.zip"))
        self.engine.tasks[task.id] = task
        self.assertIsNone(self.watcher.current_offer())

    def test_notification_click_opens_the_add_dialog(self):
        self.copy("https://example.com/a.pdf")
        self.watcher.request_open()
        self.assertEqual(self.events[-1][0], "clipboard_open")
        self.assertTrue(self.events[-1][1]["open"])
        self.assertTrue(self.watcher.current_offer()["open"], "kept for a window that is still loading")

        self.watcher.dismiss()
        self.assertIsNone(self.watcher.current_offer())
        self.watcher.request_open()  # nothing waiting: nothing happens
        self.assertEqual(self.events[-1][0], "clipboard_open")
        self.assertEqual(len(self.events), 2)


class TestClipboardApi(WatcherTestCase):
    def test_offer_and_dismiss(self):
        api = TestClient(create_app(self.engine, remote_token="t", clipboard=self.watcher),
                         base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
        self.assertEqual(api.get("/api/clipboard").json(), {"link": None})
        self.copy("https://example.com/b.7z")
        self.assertEqual(api.get("/api/clipboard").json()["link"]["filename"], "b.7z")
        api.post("/api/clipboard/dismiss")
        self.assertEqual(api.get("/api/clipboard").json(), {"link": None})

    def test_without_a_watcher(self):
        api = TestClient(create_app(self.engine, remote_token="t"),
                         base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
        self.assertEqual(api.get("/api/clipboard").json(), {"link": None})
        self.assertEqual(api.post("/api/clipboard/dismiss").status_code, 200)


@unittest.skipUnless(sys.platform == "win32", "pystray's Windows backend")
class TestNotificationClick(unittest.TestCase):
    def test_click_runs_the_notification_action(self):
        import pystray
        from PIL import Image
        from pystray._util import win32
        from openhydro.core.tray import SystemTrayManager, NIN_BALLOONUSERCLICK

        shown = mock.Mock()
        tray = SystemTrayManager(engine=None, on_show_window=shown)
        # Created but never run: no icon appears next to the clock
        tray.icon = pystray.Icon("openhydro-test", Image.new("RGBA", (16, 16)))
        original = mock.Mock(return_value=0)
        tray.icon._message_handlers[win32.WM_NOTIFY] = original
        tray._route_notification_clicks()
        on_notify = tray.icon._message_handlers[win32.WM_NOTIFY]

        on_notify(0, NIN_BALLOONUSERCLICK)
        shown.assert_called_once()  # no action given: shows the window

        action = mock.Mock()
        with mock.patch.object(tray.icon, "notify"):
            tray.notify("x" * 400, "t" * 100, on_click=action)
            message, title = tray.icon.notify.call_args.args
        self.assertEqual((len(message), len(title)), (255, 63))
        on_notify(0, NIN_BALLOONUSERCLICK)
        action.assert_called_once()

        on_notify(0, win32.WM_LBUTTONUP)
        original.assert_called_once_with(0, win32.WM_LBUTTONUP)


@unittest.skipUnless(sys.platform == "win32", "Windows clipboard")
class TestWindowsClipboard(unittest.TestCase):
    def test_sequence_number(self):
        self.assertIsInstance(WindowsClipboard().sequence(), int)

    @unittest.skipUnless(os.environ.get("OPENHYDRO_CLIPBOARD_TESTS") == "1", "replaces the clipboard: CI only")
    def test_reads_copied_text_but_not_private_copies(self):
        write_clipboard("https://example.com/setup.exe")
        self.assertEqual(WindowsClipboard().read_text(), "https://example.com/setup.exe")
        # What password managers do when copying a password
        write_clipboard("https://example.com/secret.zip", private=True)
        self.assertIsNone(WindowsClipboard().read_text())


def write_clipboard(text: str, private: bool = False):
    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT

    def global_text(value: str):
        data = (value + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
        kernel32.GlobalUnlock(handle)
        return handle

    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise unittest.SkipTest("the clipboard is not available here")
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(WindowsClipboard.CF_UNICODETEXT, global_text(text))
        if private:
            fmt = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
            user32.SetClipboardData(fmt, global_text(""))
    finally:
        user32.CloseClipboard()


if __name__ == "__main__":
    unittest.main()
