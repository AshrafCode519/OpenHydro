"""
Speed limit tests for OpenHydro
The token bucket (including reads bigger than the limit), a download's own limit on top of the global
one, and how each kind of download gets it: HTTP segments, yt-dlp, and torrents through aria2.
"""

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

from helpers import make_engine
from test_engine_downloads import MB, EngineDownloadTestCase
from test_video_and_hls import FakeYoutubeDL
from openhydro.api import create_app
from openhydro.core import DownloadTask, RateLimiter, TaskStatus
from openhydro.core.extractor import VideoExtractor
from openhydro.core.limiter import LimiterChain
from openhydro.core.torrent import TorrentManager

KB = 1024


class TestTokenBucket(unittest.IsolatedAsyncioTestCase):
    async def test_reads_bigger_than_the_limit_go_through_at_the_limit(self):
        # These used to wait forever: the bucket never holds more than one second's worth
        limiter = RateLimiter(200 * KB)
        started = time.monotonic()
        for _ in range(2):
            await asyncio.wait_for(limiter.acquire(500 * KB), timeout=10)
        # A full bucket, then 500 KB of debt paid back at 200 KB/s before the second read
        self.assertAlmostEqual(time.monotonic() - started, 2.5, delta=0.6)

    def test_blocking_version_for_threads(self):
        limiter = RateLimiter(400 * KB)
        started = time.monotonic()
        for _ in range(8):
            limiter.acquire_blocking(100 * KB)
        self.assertAlmostEqual(time.monotonic() - started, 1.0, delta=0.4)

    async def test_the_slower_limit_wins(self):
        chain = LimiterChain(RateLimiter(0), RateLimiter(400 * KB))  # unlimited global, limited download
        started = time.monotonic()
        for _ in range(8):
            await chain.acquire(100 * KB)
        self.assertGreater(time.monotonic() - started, 0.7)

    async def test_lifting_the_limit_releases_a_waiting_read(self):
        limiter = RateLimiter(10 * KB)
        await limiter.acquire(10 * KB)  # empty bucket: the next read would wait about 10 s
        waiting = asyncio.create_task(limiter.acquire(100 * KB))
        await asyncio.sleep(0.2)
        limiter.set_limit(0)
        await asyncio.wait_for(waiting, timeout=1)


class TestDownloadLimit(EngineDownloadTestCase):
    payload_size = int(1.5 * MB)

    async def test_held_to_its_own_limit(self):
        engine = make_engine(self, self.download_dir)
        task = await engine.add_download(self.serve(), num_connections=4, auto_start=False)
        await engine.set_task_speed_limit(task.id, 512)
        started = time.monotonic()
        await engine.start_task(task.id)
        await asyncio.wait_for(engine._running_loops[task.id], timeout=30)
        self.assertDownloadedIntact(task)
        # 512 KB at once (a full bucket), then 1 MB at 512 KB/s
        self.assertGreater(time.monotonic() - started, 1.6)

    async def test_a_new_limit_applies_to_a_running_download(self):
        self.payload = os.urandom(3 * MB)
        engine = make_engine(self, self.download_dir)
        task = await engine.add_download(self.serve(), num_connections=2, auto_start=False)
        await engine.set_task_speed_limit(task.id, 128)  # ~23 s for 3 MB
        await engine.start_task(task.id)
        await asyncio.sleep(0.5)
        await engine.set_task_speed_limit(task.id, 0)
        await asyncio.wait_for(engine._running_loops[task.id], timeout=10)
        self.assertDownloadedIntact(task)

    async def test_kept_across_a_restart(self):
        engine = make_engine(self, self.download_dir)
        task = await engine.add_download(self.serve(), auto_start=False)
        await engine.set_task_speed_limit(task.id, 2048)
        restored = make_engine(self, self.download_dir).tasks[task.id]
        self.assertEqual(restored.speed_limit_kbps, 2048)


class LimitedFakeYoutubeDL(FakeYoutubeDL):
    """Reports a 500 KB video in 100 KB blocks, as yt-dlp's progress hooks do."""

    def download(self, urls):
        for done in range(100 * KB, 600 * KB, 100 * KB):
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "filename": "Clip.f137.mp4", "downloaded_bytes": done,
                      "total_bytes": 500 * KB})
        super().download(urls)


class TestVideoLimit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        FakeYoutubeDL.produces = [".mp4"]

    def run_download(self, rate_limiter):
        started = time.monotonic()
        with mock.patch("yt_dlp.YoutubeDL", LimitedFakeYoutubeDL):
            result = VideoExtractor._sync_download("https://www.youtube.com/watch?v=abc", os.path.join(self.dir, "Clip.mp4"),
                                                   "best", 8, None, None, rate_limiter=rate_limiter)
        self.assertIsNotNone(result)
        return time.monotonic() - started

    def test_yt_dlp_downloads_are_held_to_the_limit(self):
        # The global limit used to skip videos entirely: only HTTP and HLS downloads went through it.
        # Unlimited first: it also pays for importing yt-dlp, which must not count against the limit
        self.assertLess(self.run_download(None), 1.5)
        # 200 KB at once (a full bucket), then 300 KB at 200 KB/s
        self.assertAlmostEqual(self.run_download(LimiterChain(RateLimiter(200 * KB))), 1.5, delta=0.5)


class TestTorrentLimits(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.mgr = TorrentManager(self.dir, rpc_secret="s")
        self.mgr.binary_path = "aria2c"
        self.calls = []
        self.mgr._call = mock.AsyncMock(side_effect=lambda method, params=None: self.calls.append((method, params)) or "gid1")

    async def test_aria2_gets_the_limits(self):
        await self.mgr.add_magnet("magnet:?xt=urn:btih:abc", save_dir=self.dir, limit=512 * KB)
        self.assertEqual(self.calls[-1], ("aria2.addUri", [["magnet:?xt=urn:btih:abc"],
                                                           {"dir": self.dir, "max-download-limit": str(512 * KB)}]))
        await self.mgr.set_download_limit("gid1", 0)
        self.assertEqual(self.calls[-1], ("aria2.changeOption", ["gid1", {"max-download-limit": "0"}]))

        self.mgr.overall_limit = 1 * MB
        self.assertIn(f"--max-overall-download-limit={MB}", self.mgr._build_command())

    async def test_global_limit_reaches_a_running_aria2(self):
        await self.mgr.set_overall_limit(256 * KB)  # not running: used when it starts
        self.assertEqual(self.calls, [])
        self.mgr._proc = mock.Mock(poll=mock.Mock(return_value=None))
        await self.mgr.set_overall_limit(256 * KB)
        self.assertEqual(self.calls, [("aria2.changeGlobalOption", [{"max-overall-download-limit": str(256 * KB)}])])
        self.mgr._proc = None

    async def test_engine_sends_a_running_torrents_limit(self):
        engine = make_engine(self, self.dir)
        task = DownloadTask(url="magnet:?xt=urn:btih:abc", filename="t", save_path=os.path.join(self.dir, "t"),
                            is_torrent=True, torrent_gid="gid9", status=TaskStatus.DOWNLOADING)
        engine.tasks[task.id] = task
        with mock.patch.object(engine.torrent_manager, "set_download_limit", mock.AsyncMock()) as set_limit:
            await engine.set_task_speed_limit(task.id, 300)
        set_limit.assert_awaited_once_with("gid9", 300 * KB)


class TestSpeedLimitApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.task = DownloadTask(url="https://example.com/a.bin", filename="a.bin",
                                 save_path=os.path.join(self.dir, "a.bin"), status=TaskStatus.PAUSED)
        self.engine.tasks[self.task.id] = self.task
        self.api = TestClient(create_app(self.engine, remote_token="t"),
                              base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def test_set_from_the_download_window(self):
        resp = self.api.post(f"/api/tasks/{self.task.id}/speed_limit", json={"max_kbps": 1024})
        self.assertEqual(resp.json(), {"max_kbps": 1024})
        self.assertEqual(self.task.speed_limit_kbps, 1024)
        self.assertEqual(self.api.post(f"/api/tasks/{self.task.id}/speed_limit", json={"max_kbps": -1}).status_code, 422)
        self.assertEqual(self.api.post("/api/tasks/nope/speed_limit", json={"max_kbps": 1}).status_code, 404)

    def test_global_limit_now_covers_torrents(self):
        self.api.post("/api/settings/limiter", json={"max_kbps": 300})
        self.assertEqual(self.engine.rate_limiter.max_bytes_per_second, 300 * KB)
        self.assertEqual(self.engine.torrent_manager.overall_limit, 300 * KB)


if __name__ == "__main__":
    unittest.main()
