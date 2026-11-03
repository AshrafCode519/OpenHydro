"""
End-to-end download engine tests against a local HTTP server
Covers dynamic splitting, chunk retries, HEAD-less size probing, and servers that ignore Range.
"""

import os
import re
import time
import shutil
import asyncio
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import make_engine
from openhydro.core import DownloadEngine, DownloadTask, Chunk, TaskStatus, DynamicSegmenter

MB = 1024 * 1024
BLOCK = 64 * 1024


class _FileHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        srv = self.server
        if srv.mode == "no_head":
            return self._empty(405)
        if srv.mode == "always_500":
            return self._empty(500)
        self.send_response(200)
        self.send_header("Content-Length", str(len(srv.payload)))
        if srv.mode != "no_range":
            self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        srv = self.server
        data = srv.payload
        start, end, partial = 0, len(data) - 1, False
        rng = self.headers.get("Range")
        if rng and srv.mode not in ("ignore_range", "no_range"):
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else len(data) - 1, len(data) - 1)
            partial = True

        if srv.stall_first_chunk and 0 < start < len(data) // 2:
            srv.split_requested.set()  # a piece split off the first chunk is being fetched

        status = srv.failure_for(start)
        if status:
            return self._empty(status)

        body = data[start:end + 1]
        self.send_response(206 if partial else 200)
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        if srv.mode != "no_range":
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            for i in range(0, len(body), BLOCK):
                self.wfile.write(body[i:i + BLOCK])
                if srv.stall_first_chunk and start == 0 and i == 4 * BLOCK:
                    # The first connection stalls until the engine splits its chunk (or gives up after
                    # 10 s), so the outcome never depends on how fast or loaded the machine is
                    srv.split_requested.wait(timeout=10)
        except OSError:
            pass  # Client closed the stream at a (dynamically moved) chunk boundary


class _TestServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, payload: bytes, mode: str = "normal", stall_first_chunk: bool = False):
        super().__init__(("127.0.0.1", 0), _FileHandler)
        self.payload = payload
        self.mode = mode
        self.stall_first_chunk = stall_first_chunk
        self.split_requested = threading.Event()
        self.flaky_left = 1
        self.failures_served = 0
        self._lock = threading.Lock()

    def handle_error(self, request, client_address):
        pass  # Connections reset by the client at chunk boundaries are expected

    def failure_for(self, start: int):
        with self._lock:
            status = None
            if self.mode == "always_500":
                status = 500
            elif self.mode == "range_403" and start > 0:
                status = 403
            elif self.mode == "flaky" and start > 0 and self.flaky_left:
                self.flaky_left -= 1
                status = 503
            if status:
                self.failures_served += 1
            return status


class EngineDownloadTestCase(unittest.IsolatedAsyncioTestCase):
    payload_size = 3 * MB

    def setUp(self):
        self.download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.download_dir, ignore_errors=True)
        self.payload = os.urandom(self.payload_size)
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def serve(self, mode="normal", stall_first_chunk=False) -> str:
        self.server = _TestServer(self.payload, mode, stall_first_chunk)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}/file.bin"

    async def download(self, url: str, connections: int = 4) -> DownloadTask:
        engine = make_engine(self, self.download_dir)
        task = await engine.add_download(url, num_connections=connections, auto_start=False)
        await engine.start_task(task.id)
        await asyncio.wait_for(engine._running_loops[task.id], timeout=60)
        return task

    def assertDownloadedIntact(self, task: DownloadTask):
        self.assertEqual(task.status, TaskStatus.COMPLETED, task.error_message)
        self.assertEqual(task.total_bytes, len(self.payload))
        with open(task.save_path, "rb") as f:
            self.assertTrue(f.read() == self.payload, "downloaded bytes differ from the source")


class TestDynamicSplitting(EngineDownloadTestCase):
    payload_size = 8 * MB

    def test_split_when_largest_chunk_is_not_last(self):
        task = DownloadTask(url="x", total_bytes=80 * MB, supports_ranges=True, num_connections=8)
        task.chunks = DynamicSegmenter.create_initial_chunks(80 * MB, 8)
        for i, c in enumerate(task.chunks):
            c.status = "completed" if i == 0 else "downloading"
            c.current_byte = c.end_byte + 1 if i == 0 else c.start_byte + (0 if i == 3 else MB)

        largest = task.chunks[3]
        original_end = largest.end_byte
        new_chunk = DynamicSegmenter.split_largest_chunk_unlocked(task)

        self.assertIsNotNone(new_chunk)
        self.assertEqual(largest.end_byte + 1, new_chunk.start_byte)
        self.assertEqual(new_chunk.end_byte, original_end)
        self.assertGreater(largest.end_byte, largest.current_byte)

    async def test_finished_workers_help_the_slow_chunk(self):
        # The first chunk's connection stalls: once the other worker is done it must split that chunk
        url = self.serve(stall_first_chunk=True)
        task = await self.download(url, connections=2)
        self.assertDownloadedIntact(task)
        self.assertGreater(len(task.chunks), 2, "no dynamic split happened")


class TestFailureHandling(EngineDownloadTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("openhydro.core.engine.RETRY_BACKOFF_BASE", 0.001)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_transient_chunk_failure_is_retried(self):
        task = await self.download(self.serve("flaky"))
        self.assertDownloadedIntact(task)
        self.assertEqual(self.server.flaky_left, 0)

    async def test_permanent_failure_reports_error(self):
        task = await self.download(self.serve("range_403"))
        self.assertEqual(task.status, TaskStatus.ERROR)
        self.assertIn("HTTP 403", task.error_message)

    async def test_expired_link_gives_up_quickly(self):
        task = await self.download(self.serve("range_403"), connections=2)
        self.assertEqual(task.status, TaskStatus.ERROR)
        self.assertLessEqual(self.server.failures_served, 3, "a 403 should not be retried a dozen times")

    async def test_long_outage_is_ridden_out(self):
        url = self.serve("flaky")
        self.server.flaky_left = 8  # more consecutive failures than the old limit of 5
        task = await self.download(url, connections=2)
        self.assertDownloadedIntact(task)

    async def test_single_stream_failure_reports_error(self):
        task = await self.download(self.serve("always_500"))
        self.assertEqual(task.status, TaskStatus.ERROR)
        self.assertIn("HTTP 500", task.error_message)


class TestServerQuirks(EngineDownloadTestCase):
    async def test_probe_without_head_reports_real_size(self):
        info = await DownloadEngine.probe_url(self.serve("no_head"))
        self.assertEqual(info["total_bytes"], len(self.payload))
        self.assertTrue(info["supports_ranges"])

    async def test_download_without_head(self):
        task = await self.download(self.serve("no_head"))
        self.assertDownloadedIntact(task)

    async def test_server_ignoring_range_falls_back_to_single_connection(self):
        task = await self.download(self.serve("ignore_range"))
        self.assertDownloadedIntact(task)
        self.assertFalse(task.supports_ranges)

    async def test_single_stream_restarts_progress(self):
        url = self.serve("no_range")
        engine = make_engine(self, self.download_dir)
        task = await engine.add_download(url, auto_start=False)
        self.assertFalse(task.supports_ranges)
        # Leftover progress from an interrupted attempt must not inflate the final size
        task.chunks = [Chunk(id=0, start_byte=0, end_byte=len(self.payload) - 1, current_byte=12345)]
        await engine.start_task(task.id)
        await asyncio.wait_for(engine._running_loops[task.id], timeout=60)
        self.assertDownloadedIntact(task)


if __name__ == "__main__":
    unittest.main()
