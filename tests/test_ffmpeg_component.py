"""
ffmpeg on demand tests for OpenHydro
Downloading OpenHydro's own ffmpeg (verified, confined to its folder, once), using it, and what a video
download does with and without it.
"""

import hashlib
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

from starlette.testclient import TestClient

from helpers import make_engine
from test_video_and_hls import FakeYoutubeDL
from openhydro.api import create_app
from openhydro.core import ffmpeg_component, DownloadTask, TaskStatus
from openhydro.core.extractor import VideoExtractor
from openhydro.core.hls import HLSDownloader

BUILD = "ffmpeg-master-latest-win64-gpl-shared"


def build_zip(extra=()):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe", "avcodec-62.dll"):
            z.writestr(f"{BUILD}/bin/{name}", f"fake {name}")
        z.writestr(f"{BUILD}/doc/readme.txt", "not needed")
        for name in extra:
            z.writestr(name, "not allowed")
    return data.getvalue()


class ComponentTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.root = os.path.join(self.dir, "components", "ffmpeg")
        self.archive = build_zip()
        self.opens = 0
        self.sha256 = hashlib.sha256(self.archive).hexdigest()
        for target, value in (
            ("component_dir", lambda: self.root),
            ("supported", lambda: True),
            ("release_asset", lambda: {"url": "https://example.com/ffmpeg.zip", "size": len(self.archive),
                                       "sha256": self.sha256}),
            ("_open", self.fake_open),
            ("_version", lambda exe: "N-123-test"),
        ):
            patcher = mock.patch.object(ffmpeg_component, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_open(self, url):
        self.opens += 1
        time.sleep(0.05)  # long enough for a second caller to arrive while this one downloads
        return io.BytesIO(self.archive)

    def files(self):
        found = []
        for folder, _, names in os.walk(self.dir):
            found += [os.path.relpath(os.path.join(folder, n), self.dir).replace("\\", "/") for n in names]
        return sorted(found)


class TestDownload(ComponentTestCase):
    def test_installs_the_bin_folder_once(self):
        progress = []
        path = ffmpeg_component.ensure(lambda done, total: progress.append((done, total)))
        self.assertEqual(path, os.path.join(self.root, "bin", "ffmpeg.exe"))
        self.assertEqual(self.files(), [
            "components/ffmpeg/bin/avcodec-62.dll", "components/ffmpeg/bin/ffmpeg.exe",
            "components/ffmpeg/bin/ffprobe.exe", "components/ffmpeg/info.json"])
        self.assertEqual(progress[-1], (len(self.archive), len(self.archive)))
        self.assertEqual(ffmpeg_component.installed_info()["version"], "N-123-test")

        self.assertEqual(ffmpeg_component.ensure(), path)
        self.assertEqual(self.opens, 1, "already installed: not downloaded again")

    def test_wrong_checksum_installs_nothing(self):
        self.sha256 = "0" * 64
        with self.assertRaises(ValueError):
            ffmpeg_component.ensure()
        self.assertEqual(self.files(), [], "no partial download or half-installed folder left")

    def test_a_build_that_does_not_run_is_not_installed(self):
        with mock.patch.object(ffmpeg_component, "_version", side_effect=RuntimeError("does not run")):
            with self.assertRaises(RuntimeError):
                ffmpeg_component.ensure()
        self.assertIsNone(ffmpeg_component.installed_path())
        self.assertEqual(self.files(), [])

    def test_archive_paths_cannot_escape(self):
        self.archive = build_zip(extra=(f"{BUILD}/bin/..\\..\\..\\evil.dll", "../../bin/evil2.dll",
                                        f"{BUILD}/bin/ffmpeg.exe:stream"))
        self.sha256 = hashlib.sha256(self.archive).hexdigest()
        ffmpeg_component.ensure()
        outside = [f for f in self.files() if not f.startswith("components/ffmpeg/")]
        self.assertEqual(outside, [])
        self.assertNotIn("components/ffmpeg/bin/evil2.dll", self.files(), "not inside the build's bin folder")

    def test_two_downloads_needing_it_fetch_it_once(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(ffmpeg_component.ensure())) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.opens, 1)
        self.assertEqual(len(set(results)), 1)

    def test_used_when_there_is_no_system_ffmpeg(self):
        with mock.patch("openhydro.core.hls.shutil.which", return_value=None), \
                mock.patch("glob.glob", return_value=[]):
            self.assertIsNone(HLSDownloader.find_ffmpeg())
            self.assertFalse(ffmpeg_component.status()["available"])
            ffmpeg_component.ensure()
            self.assertEqual(HLSDownloader.find_ffmpeg(), ffmpeg_component.installed_path())
            self.assertEqual(ffmpeg_component.status()["source"], "openhydro")


class TestReleaseAsset(unittest.TestCase):
    def release(self, digest="sha256:" + "a" * 64, name=ffmpeg_component.ASSET_NAME):
        return {"assets": [{"name": "checksums.sha256"}, {
            "name": name, "size": 91_000_000, "digest": digest,
            "browser_download_url": f"https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/{name}"}]}

    def test_uses_githubs_checksum(self):
        with mock.patch.object(ffmpeg_component, "_get_json", return_value=self.release()):
            asset = ffmpeg_component.release_asset()
        self.assertEqual(asset["sha256"], "a" * 64)
        self.assertTrue(asset["url"].endswith(ffmpeg_component.ASSET_NAME))

    def test_refuses_a_build_without_checksum_or_missing(self):
        for release in (self.release(digest=None), self.release(digest="md5:abc"), self.release(name="other.zip")):
            with mock.patch.object(ffmpeg_component, "_get_json", return_value=release):
                with self.assertRaises(RuntimeError):
                    ffmpeg_component.release_asset()


class TestFormatsWithoutFfmpeg(unittest.TestCase):
    def test_which_requests_need_it(self):
        for fmt, needs in (("bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", True),
                           ("audio_mp3", True), (None, True), ("audio_m4a", False), ("subtitle_srt", False),
                           ("best[height<=720]/best", False)):
            self.assertEqual(VideoExtractor.needs_ffmpeg(fmt), needs, fmt)

    def test_falls_back_to_single_file_formats(self):
        self.assertEqual(VideoExtractor.without_merging(
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"), "best[height<=1080]/best")
        self.assertEqual(VideoExtractor.without_merging("hd/bestvideo+bestaudio/best"), "hd/best")
        self.assertEqual(VideoExtractor.without_merging("bestvideo+bestaudio"), "best")
        self.assertEqual(VideoExtractor.without_merging("audio_mp3"), "audio_m4a")
        self.assertEqual(VideoExtractor.without_merging("subtitle_srt"), "subtitle_srt")

    def test_yt_dlp_gets_no_merge_without_ffmpeg(self):
        # yt-dlp aborts a merge (or an MP3) when ffmpeg is missing: the download must not ask for one
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        FakeYoutubeDL.produces = [".mp4"]
        with mock.patch("yt_dlp.YoutubeDL", FakeYoutubeDL), \
                mock.patch.object(HLSDownloader, "find_ffmpeg", return_value=None):
            VideoExtractor._sync_download("https://www.youtube.com/watch?v=abc", os.path.join(folder, "Clip.mp4"),
                                          "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", 8, None, None)
            self.assertEqual(FakeYoutubeDL.last_opts["format"], "best[height<=1080]/best")
            VideoExtractor._sync_download("https://www.youtube.com/watch?v=abc", os.path.join(folder, "Song.mp3"),
                                          "audio_mp3", 8, None, None)
            self.assertNotIn("postprocessors", FakeYoutubeDL.last_opts, "no MP3 conversion without ffmpeg")


class TestVideoDownloadPreparesFfmpeg(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.events = []
        self.engine.register_listener(lambda event, data: self.events.append((event, data.get("phase"))))
        self.task = DownloadTask(url="https://www.youtube.com/watch?v=abc", filename="Clip.mp4",
                                 save_path=os.path.join(self.dir, "Clip.mp4"), status=TaskStatus.DOWNLOADING,
                                 is_extractor=True, selected_format="bestvideo+bestaudio/best")
        self.engine.tasks[self.task.id] = self.task
        self.download = mock.AsyncMock(return_value=self.task.save_path)
        for patcher in (mock.patch.object(VideoExtractor, "download", self.download),
                        mock.patch.object(HLSDownloader, "find_ffmpeg", return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_downloads_ffmpeg_first(self):
        def ensure(on_progress):
            on_progress(45 * 2**20, 90 * 2**20)
            return "ffmpeg.exe"

        with mock.patch.object(ffmpeg_component, "supported", return_value=True), \
                mock.patch.object(ffmpeg_component, "ensure", side_effect=ensure) as ensure_mock:
            await self.engine._execute_extractor_download(self.task)
        ensure_mock.assert_called_once()
        self.download.assert_awaited_once()
        phases = [phase for _, phase in self.events if phase]
        self.assertIn("تجهيز ffmpeg لأول مرة (دمج الصوت والصورة): 50% من 90 MB", phases)
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)
        self.assertIsNone(self.task.phase)

    async def test_without_ffmpeg_the_download_still_runs(self):
        with mock.patch.object(ffmpeg_component, "supported", return_value=True), \
                mock.patch.object(ffmpeg_component, "ensure", side_effect=OSError("offline")):
            await self.engine._execute_extractor_download(self.task)
        self.download.assert_awaited_once()
        self.assertIn("ffmpeg غير متاح: التنزيل بأفضل جودة لا تحتاج دمج", [p for _, p in self.events])
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    async def test_says_why_when_nothing_could_be_downloaded(self):
        # No ffmpeg and no single-file format (e.g. YouTube without a JS runtime): explain, not "failed"
        self.download.return_value = None
        with mock.patch.object(ffmpeg_component, "supported", return_value=True), \
                mock.patch.object(ffmpeg_component, "ensure", side_effect=OSError("GitHub blocked")):
            await self.engine._execute_extractor_download(self.task)
        self.assertEqual(self.task.status, TaskStatus.ERROR)
        self.assertIn("محتاج ffmpeg", self.task.error_message)

        self.task.status = TaskStatus.DOWNLOADING
        with mock.patch.object(HLSDownloader, "find_ffmpeg", return_value="ffmpeg.exe"):
            await self.engine._execute_extractor_download(self.task)
        self.assertEqual(self.task.error_message, "فشل تحميل الفيديو من المنصة", "other failures keep the usual message")

    async def test_not_needed_for_original_audio(self):
        self.task.selected_format = "audio_m4a"
        with mock.patch.object(ffmpeg_component, "ensure") as ensure_mock:
            await self.engine._execute_extractor_download(self.task)
        ensure_mock.assert_not_called()


class TestFfmpegApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)
        self.events = []
        self.engine.register_listener(lambda event, data: self.events.append(event))
        self.api = TestClient(create_app(self.engine, remote_token="t"),
                              base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def status(self, **values):
        return {"supported": True, "available": False, "source": None, "version": None,
                "installing": False, "progress": None, **values}

    def test_install_from_the_settings(self):
        with mock.patch.object(ffmpeg_component, "status", return_value=self.status()), \
                mock.patch.object(ffmpeg_component, "ensure", return_value="ffmpeg.exe") as ensure_mock:
            with self.api:  # runs the app's event loop, so the background install can finish
                self.assertTrue(self.api.post("/api/components/ffmpeg/install").json()["installing"])
                deadline = time.monotonic() + 5
                while "component_ready" not in self.events and time.monotonic() < deadline:
                    time.sleep(0.02)
        ensure_mock.assert_called_once()
        self.assertIn("component_ready", self.events)

    def test_not_offered_where_it_cannot_be_downloaded(self):
        with mock.patch.object(ffmpeg_component, "status", return_value=self.status(supported=False)):
            self.assertEqual(self.api.post("/api/components/ffmpeg/install").status_code, 400)

    def test_nothing_to_do_when_present(self):
        with mock.patch.object(ffmpeg_component, "status", return_value=self.status(available=True)), \
                mock.patch.object(ffmpeg_component, "ensure") as ensure_mock:
            self.assertTrue(self.api.post("/api/components/ffmpeg/install").json()["available"])
        ensure_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
