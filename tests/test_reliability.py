"""
Reliability tests for OpenHydro
Video downloads that lose their connection (found in a user's log: YouTube stalled, yt-dlp gave up, the
video failed for good), the reasons shown when one fails, the log, and the WebSocket implementation.
"""

import io
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import make_engine
from test_video_and_hls import FakeYoutubeDL
from openhydro.core import DownloadTask, TaskStatus, engine as engine_module
from openhydro.core.extractor import VideoDownloadError, VideoExtractor
from openhydro.core.hls import HLSDownloader

READ_TIMEOUT = ("ERROR: [download] Got error: HTTPSConnectionPool(host='rr1---sn-x.googlevideo.com', port=443): "
                "Read timed out. (read timeout=20.0)")


class TestFailureReasons(unittest.TestCase):
    def test_network_failures_are_worth_another_try(self):
        for reason in (READ_TIMEOUT, "ERROR: Connection reset by peer", "HTTP Error 503: Service Unavailable",
                       "[WinError 10054] An existing connection was forcibly closed by the remote host"):
            error = VideoDownloadError(reason)
            self.assertTrue(error.transient, reason)
            self.assertIn("انقطع الاتصال", error.message)

    def test_reasons_the_user_can_act_on(self):
        for reason, expected in (
                ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot", "مش برنامج آلي"),
                ("ERROR: [youtube] abc: Video unavailable", "مش متاح"),
                ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "مش متاح"),
                ("ERROR: [youtube] abc: Requested format is not available", "الجودة اللي اخترتها"),
                ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "403")):
            error = VideoDownloadError(reason)
            self.assertIn(expected, error.message, reason)
            self.assertFalse(error.transient, reason)

    def test_unknown_reasons_are_shown_rather_than_hidden(self):
        error = VideoDownloadError("ERROR: something new went wrong")
        self.assertEqual(error.message, "فشل تحميل الفيديو: something new went wrong")
        self.assertFalse(error.transient)


class FailingYoutubeDL(FakeYoutubeDL):
    def download(self, urls):
        import yt_dlp
        raise yt_dlp.utils.DownloadError(READ_TIMEOUT)


class TestYtDlpFailures(unittest.TestCase):
    def test_the_reason_reaches_the_engine(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with mock.patch("yt_dlp.YoutubeDL", FailingYoutubeDL), \
                mock.patch.object(HLSDownloader, "find_ffmpeg", return_value="ffmpeg.exe"):
            with self.assertRaises(VideoDownloadError) as caught:
                VideoExtractor._sync_download("https://www.youtube.com/watch?v=abc", os.path.join(folder, "v.mp4"),
                                              "best", 8, None, None)
        self.assertTrue(caught.exception.transient)
        # yt-dlp waits longer before each of its own retries instead of retrying at once
        sleep = FailingYoutubeDL.last_opts["retry_sleep_functions"]["http"]
        self.assertEqual([sleep(n) for n in (0, 1, 2, 5, 9)], [1, 2, 4, 30, 30])


class TestVideoRetries(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.phases = []
        self.engine.register_listener(lambda event, data: self.phases.append(data.get("phase")))
        self.task = DownloadTask(url="https://www.youtube.com/watch?v=abc", filename="v.mp4",
                                 save_path=os.path.join(self.dir, "v.mp4"), status=TaskStatus.DOWNLOADING,
                                 is_extractor=True, selected_format="best")
        self.engine.tasks[self.task.id] = self.task
        for patcher in (mock.patch.object(engine_module, "VIDEO_RETRY_DELAYS", (0.01, 0.01, 0.01, 0.01)),
                        mock.patch.object(HLSDownloader, "find_ffmpeg", return_value="ffmpeg.exe")):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_download(self, *outcomes):
        def attempt(**options):
            outcome = outcomes[min(download.call_count - 1, len(outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        download = mock.AsyncMock(side_effect=attempt)
        with mock.patch.object(VideoExtractor, "download", download):
            await self.engine._execute_extractor_download(self.task)
        return download.call_count

    async def test_a_lost_connection_is_tried_again_and_the_video_finishes(self):
        lost = VideoDownloadError(READ_TIMEOUT)
        self.assertEqual(await self.run_download(lost, lost, self.task.save_path), 3)
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)
        self.assertTrue(any(p and "محاولة جديدة" in p for p in self.phases), "the row says it will try again")
        self.assertIsNone(self.task.phase)

    async def test_a_removed_video_is_not_tried_again(self):
        self.assertEqual(await self.run_download(VideoDownloadError("ERROR: Video unavailable")), 1)
        self.assertEqual(self.task.status, TaskStatus.ERROR)
        self.assertIn("مش متاح", self.task.error_message)

    async def test_gives_up_after_a_few_tries_with_the_reason(self):
        self.assertEqual(await self.run_download(VideoDownloadError(READ_TIMEOUT)), 5)
        self.assertEqual(self.task.status, TaskStatus.ERROR)
        self.assertIn("اضغط استئناف", self.task.error_message)

    async def test_pausing_during_the_wait_stops_it(self):
        def lost_then_paused(**options):
            self.task.status = TaskStatus.PAUSED  # the user pressed pause while it waited
            raise VideoDownloadError(READ_TIMEOUT)

        with mock.patch.object(VideoExtractor, "download", mock.AsyncMock(side_effect=lost_then_paused)) as download:
            await self.engine._execute_extractor_download(self.task)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.task.status, TaskStatus.PAUSED)


class TestLog(unittest.TestCase):
    def setUp(self):
        import run
        self.run = run
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_every_line_has_its_time(self):
        out = io.StringIO()
        log = self.run.TimestampedLog(out)
        log.write("first line\nsecond ")
        log.write("line\n")
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        for line, text in zip(lines, ("first line", "second line")):
            self.assertRegex(line, r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] " + re.escape(text) + "$")

    def test_starts_over_past_its_size_keeping_one_previous(self):
        path = os.path.join(self.dir, "openhydro.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * (self.run.LOG_MAX_BYTES + 1))
        log = self.run.open_log(self.dir)
        log.write("after\n")
        log.flush()
        log._stream.close()
        self.assertGreater(os.path.getsize(path + ".1"), self.run.LOG_MAX_BYTES)
        self.assertLess(os.path.getsize(path), 100)


class TestLeftoverProfiles(unittest.TestCase):
    """Folders the window's private WebView2 profile left in TEMP, removed using the app's own log."""

    def setUp(self):
        import run
        self.run = run
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.temp = os.path.join(self.root, "Temp")
        self.logs = os.path.join(self.root, "OpenHydro")
        os.makedirs(self.logs)

    def profile(self, *parts):
        folder = os.path.join(*parts)
        os.makedirs(os.path.join(folder, "EBWebView", "Default"))
        return folder

    def logged(self, folder):
        # Exactly as pywebview logs it: the error's text, whose path is a repr (doubled backslashes)
        error = PermissionError(13, "Access is denied", os.path.join(folder, "EBWebView", "BrowserMetrics", "a.pma"))
        return f"[pywebview] Failed to delete user data folder: {error}\n"

    def test_only_folders_the_log_names_in_temp_are_removed(self):
        left_behind = self.profile(self.temp, "tmpa34n8vi8")
        not_in_the_log = self.profile(self.temp, "tmpzzzzzzzz")  # maybe another app's
        elsewhere = self.profile(self.root, "Elsewhere", "tmpb12c")  # named in the log, but not in TEMP
        with open(os.path.join(self.logs, "openhydro.log.1"), "w", encoding="utf-8") as f:
            f.write(self.logged(left_behind) + self.logged(elsewhere))
        with open(os.path.join(self.logs, "openhydro.log"), "w", encoding="utf-8") as f:
            f.write("[OpenHydro] Launching native desktop window...\n" + self.logged(left_behind))

        self.assertEqual(self.run.remove_leftover_profiles(self.logs, temp_dir=self.temp), 1)
        self.assertFalse(os.path.exists(left_behind))
        self.assertTrue(os.path.exists(not_in_the_log))
        self.assertTrue(os.path.exists(elsewhere))

    def test_no_log_nothing_to_do(self):
        self.assertEqual(self.run.remove_leftover_profiles(os.path.join(self.root, "missing"), temp_dir=self.temp), 0)


class TestWebSocketImplementation(unittest.TestCase):
    def test_not_the_deprecated_one(self):
        from uvicorn.config import WS_PROTOCOLS
        from openhydro.api import websocket_implementation
        expected = "websockets-sansio" if "websockets-sansio" in WS_PROTOCOLS else "websockets"
        self.assertEqual(websocket_implementation(), expected)


if __name__ == "__main__":
    unittest.main()
