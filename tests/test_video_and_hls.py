"""
Video platform, Google Drive and HLS tests for OpenHydro
yt-dlp and Google Drive are faked; HLS runs against a local HTTP server.
"""

import os
import re
import shutil
import asyncio
import tempfile
import threading
import subprocess
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from helpers import make_engine
from openhydro.core import DownloadTask, TaskStatus, HLSStreamParser, HLSDownloader
from openhydro.core.extractor import VideoExtractor
from openhydro.core.gdrive import GoogleDriveHandler


class TestVideoSiteDetection(unittest.TestCase):
    def test_whole_domains_only(self):
        for url in ("https://x.com/user/status/1", "https://www.youtube.com/watch?v=abc",
                    "https://m.facebook.com/watch/?v=1", "https://vm.tiktok.com/ZM8abc/"):
            self.assertTrue(VideoExtractor.is_video_site(url), url)
        for url in ("https://www.dropbox.com/s/abc/file.zip", "https://www.netflix.com/title/1",
                    "https://cdn.linux.com/distro.iso", "https://notyoutube.com/video.mp4"):
            self.assertFalse(VideoExtractor.is_video_site(url), url)

    def test_list_parameter_must_match_exactly(self):
        self.assertTrue(VideoExtractor.is_playlist_url("https://www.youtube.com/watch?v=1&list=PL123"))
        self.assertFalse(VideoExtractor.is_playlist_url("https://example.com/file.zip?checklist=1"))
        self.assertFalse(VideoExtractor.is_playlist_url("https://example.com/get?playlist=summer"))


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL: creates the files listed in `produces` next to the template."""
    produces = []
    last_opts = None

    def __init__(self, opts):
        self.opts = opts
        FakeYoutubeDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        base = self.opts["outtmpl"][: -len(".%(ext)s")].replace("%%", "%")
        for suffix in FakeYoutubeDL.produces:
            with open(base + suffix, "wb") as f:
                f.write(b"media")


class TestExtractorOutput(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patcher = mock.patch("yt_dlp.YoutubeDL", FakeYoutubeDL)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_download(self, name: str, fmt: str, produces):
        FakeYoutubeDL.produces = produces
        return VideoExtractor._sync_download("https://www.youtube.com/watch?v=abc", os.path.join(self.dir, name),
                                             fmt, 8, None, None)

    def test_real_container_is_kept_and_other_files_untouched(self):
        other = os.path.join(self.dir, "Clip 2.mp4")  # another download sharing the "Clip" prefix
        with open(other, "wb") as f:
            f.write(b"someone else's video")

        result = self.run_download("Clip.mp4", "bestvideo+bestaudio/best", [".webm", ".f137.mp4", ".mp4.part"])

        self.assertEqual(result, os.path.join(self.dir, "Clip.webm"))
        with open(other, "rb") as f:
            self.assertEqual(f.read(), b"someone else's video")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "Clip.mp4")))

    def test_percent_in_title_is_escaped(self):
        result = self.run_download("100% Real.mp4", "bestvideo+bestaudio/best", [".mp4"])
        self.assertTrue(FakeYoutubeDL.last_opts["outtmpl"].endswith("100%% Real.%(ext)s"))
        self.assertEqual(result, os.path.join(self.dir, "100% Real.mp4"))

    def test_compatible_codecs_are_preferred_without_lowering_resolution(self):
        self.run_download("Clip.mp4", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", [".mp4"])
        # Resolution first: H.264/AAC only win among formats of the chosen size (no 4K -> 1080p downgrade)
        self.assertEqual(FakeYoutubeDL.last_opts["format_sort"], ["res:1080", "vcodec:h264", "acodec:aac"])
        self.run_download("Clip.mp4", "bestvideo+bestaudio/best", [".mp4"])
        self.assertEqual(FakeYoutubeDL.last_opts["format_sort"], ["res", "vcodec:h264", "acodec:aac"])

    def test_m4a_request_keeps_real_audio_extension(self):
        result = self.run_download("Song.m4a", "audio_m4a", [".opus"])
        self.assertEqual(result, os.path.join(self.dir, "Song.opus"))

    def test_nothing_produced_is_a_failure(self):
        self.assertIsNone(self.run_download("Clip.mp4", "bestvideo+bestaudio/best", [".f137.mp4"]))


class TestEngineUsesRealOutputPath(unittest.IsolatedAsyncioTestCase):
    async def test_task_points_at_produced_file(self):
        download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, download_dir, ignore_errors=True)
        engine = make_engine(self, download_dir)
        produced = os.path.join(download_dir, "Clip.webm")

        async def fake_download(**kwargs):
            with open(produced, "wb") as f:
                f.write(b"x" * 1234)
            return produced

        task = DownloadTask(url="https://www.youtube.com/watch?v=abc", filename="Clip.mp4",
                            save_path=os.path.join(download_dir, "Clip.mp4"), is_extractor=True)
        engine.tasks[task.id] = task
        with mock.patch("openhydro.core.engine.VideoExtractor.download", new=fake_download):
            await engine.start_task(task.id)
            await asyncio.wait_for(engine._running_loops[task.id], timeout=10)

        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.save_path, produced)
        self.assertEqual(task.filename, "Clip.webm")
        self.assertEqual(task.total_bytes, 1234)


class _TrackingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.consumed = False

    async def __aiter__(self):
        self.consumed = True
        yield b"x" * 65536


class TestGoogleDriveProbe(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, handler):
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with mock.patch("openhydro.core.gdrive.httpx.AsyncClient",
                        side_effect=lambda *a, **kw: real_client(*a, transport=transport, **kw)):
            return await GoogleDriveHandler.resolve_direct_download("https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view")

    async def test_direct_file_is_not_downloaded_to_probe_it(self):
        body = _TrackingStream()

        def handler(request):
            return httpx.Response(200, headers={
                "content-type": "application/zip",
                "content-disposition": 'attachment; filename="big.zip"',
                "content-length": "52428800",
            }, stream=body)

        info = await self.resolve(handler)
        self.assertEqual(info["filename"], "big.zip")
        self.assertEqual(info["total_bytes"], 52428800)
        self.assertFalse(body.consumed, "the probe read the file body")

    async def test_virus_scan_page_is_confirmed(self):
        page = ('<html><form id="download-form" action="https://drive.usercontent.google.com/download">'
                '<input type="hidden" name="id" value="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs">'
                '<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="u-1"></form></html>')

        def handler(request):
            if request.url.host == "drive.google.com":
                return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=page)
            self.assertEqual(request.headers.get("range"), "bytes=0-0")
            return httpx.Response(206, headers={
                "content-range": "bytes 0-0/7340032",
                "content-disposition": 'attachment; filename="movie.mkv"',
            }, content=b"x")

        info = await self.resolve(handler)
        self.assertEqual(info["filename"], "movie.mkv")
        self.assertEqual(info["total_bytes"], 7340032)
        self.assertTrue(info["resolved_url"].startswith("https://drive.usercontent.google.com/download?"))
        self.assertIn("uuid=u-1", info["resolved_url"])


class TestPlaylistFlags(unittest.TestCase):
    def test_encryption_and_fmp4_are_detected(self):
        base = "https://cdn.example.com/s/index.m3u8"
        plain = HLSStreamParser.parse_content("#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:2,\na.ts\n", base)
        aes = HLSStreamParser.parse_content('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:2,\na.ts\n', base)
        fmp4 = HLSStreamParser.parse_content('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2,\na.m4s\n', base)
        self.assertFalse(plain["encrypted"] or plain["fmp4"])
        self.assertTrue(aes["encrypted"])
        self.assertTrue(fmp4["fmp4"])


class _HLSHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        srv = self.server
        srv.requests.append((self.path, self.headers))  # case-insensitive lookups
        if self.path.startswith("/index.m3u8"):
            body = srv.playlist.encode()
        else:
            idx = int(re.match(r"/seg(\d+)\.ts", self.path).group(1))
            if idx in srv.missing:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = srv.segments[idx]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


class _HLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, segments, missing=(), key_line=""):
        super().__init__(("127.0.0.1", 0), _HLSHandler)
        self.segments = segments
        self.missing = set(missing)
        self.requests = []
        entries = "".join(f"#EXTINF:2.0,\nseg{i}.ts\n" for i in range(len(segments)))
        self.playlist = f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n{key_line}{entries}#EXT-X-ENDLIST\n"

    def handle_error(self, request, client_address):
        pass


class TestHLSDownloads(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.segments = [bytes([i]) * (128 * 1024) for i in range(4)]

    def serve(self, **kwargs) -> str:
        server = _HLSServer(self.segments, **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.server = server
        return f"http://127.0.0.1:{server.server_address[1]}/index.m3u8"

    async def run_hls_task(self, url: str) -> DownloadTask:
        engine = make_engine(self, self.dir)
        task = DownloadTask(url=url, filename="stream.mp4", save_path=os.path.join(self.dir, "stream.mp4"),
                            is_hls=True, num_connections=4, referrer="https://site.example/watch",
                            custom_headers={"X-Token": "abc"})
        engine.tasks[task.id] = task
        await engine.start_task(task.id)
        await asyncio.wait_for(engine._running_loops[task.id], timeout=30)
        return task

    async def test_segments_get_headers_and_are_joined(self):
        with mock.patch("openhydro.core.hls.HLSDownloader.find_ffmpeg", return_value=None):
            task = await self.run_hls_task(self.serve())

        self.assertEqual(task.status, TaskStatus.COMPLETED, task.error_message)
        self.assertTrue(task.filename.endswith(".ts"), "TS data must not be labelled .mp4 without FFmpeg")
        with open(task.save_path, "rb") as f:
            self.assertTrue(f.read() == b"".join(self.segments))
        segment_requests = [h for path, h in self.server.requests if path.startswith("/seg")]
        self.assertEqual(len(segment_requests), 4)
        for headers in segment_requests:
            self.assertEqual(headers.get("Referer"), "https://site.example/watch")
            self.assertEqual(headers.get("X-Token"), "abc")

    async def test_missing_segment_is_an_error(self):
        task = await self.run_hls_task(self.serve(missing={2}))
        self.assertEqual(task.status, TaskStatus.ERROR)
        self.assertIn("1 من 4", task.error_message)

    async def test_interrupted_segment_is_downloaded_again(self):
        url = self.serve()
        segments = HLSStreamParser.parse_content(self.server.playlist, url)["segments"]
        temp_dir = os.path.join(self.dir, "segments")

        def cancel_once_first_segment_has_data():
            # Simulates a pause mid-way through segment 0 (it is 4 blocks long)
            for name in ("segment_00000.ts", "segment_00000.ts.tmp"):
                path = os.path.join(temp_dir, name)
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    return True
            return False

        await HLSDownloader.download_segments(segments, temp_dir, num_connections=1,
                                              is_cancelled=cancel_once_first_segment_has_data)
        result = await HLSDownloader.download_segments(segments, temp_dir, num_connections=1)

        self.assertTrue(all(result))
        for idx, path in enumerate(result):
            with open(path, "rb") as f:
                self.assertTrue(f.read() == self.segments[idx], f"segment {idx} is corrupted")

    async def test_encrypted_stream_is_handed_to_ytdlp(self):
        url = self.serve(key_line='#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n')
        calls = {}

        async def fake_download(**kwargs):
            calls.update(kwargs)
            with open(kwargs["output_path"], "wb") as f:
                f.write(b"decrypted")
            return kwargs["output_path"]

        with mock.patch("openhydro.core.engine.VideoExtractor.download", new=fake_download):
            task = await self.run_hls_task(url)

        self.assertEqual(task.status, TaskStatus.COMPLETED, task.error_message)
        self.assertEqual(calls["url"], url)
        self.assertEqual(calls["http_headers"]["Referer"], "https://site.example/watch")
        self.assertFalse(any(path.startswith("/seg") for path, _ in self.server.requests),
                         "encrypted segments must not be concatenated as-is")


@unittest.skipUnless(HLSDownloader.find_ffmpeg(), "needs FFmpeg")
class TestFFmpegRemux(unittest.TestCase):
    def test_paths_with_apostrophes_are_remuxed(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        folder = os.path.join(root, "Don't Stop")
        os.makedirs(folder)
        ffmpeg = HLSDownloader.find_ffmpeg()
        segments = []
        for i in range(2):
            seg = os.path.join(folder, f"seg{i}.ts")
            subprocess.run([ffmpeg, "-y", "-loglevel", "error",
                            "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
                            "-f", "lavfi", "-i", "sine=duration=1",
                            "-c:v", "mpeg2video", "-c:a", "aac", "-shortest", "-f", "mpegts", seg], check=True)
            segments.append(seg)

        out = HLSDownloader.assemble_stream(segments, os.path.join(folder, "out.mp4"))

        self.assertEqual(out, os.path.join(folder, "out.mp4"))
        self.assertGreater(os.path.getsize(out), 0)
        self.assertEqual([f for f in os.listdir(folder) if f.startswith("concat_list_")], [])


if __name__ == "__main__":
    unittest.main()
