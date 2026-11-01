"""
Comprehensive Unit Tests for OpenHydro
Tests dynamic segmentation, rate limiting, task state, and storage persistence.
"""

import os
import shutil
import asyncio
import unittest

from openhydro.core import (
    DownloadTask,
    Chunk,
    TaskStatus,
    DynamicSegmenter,
    StorageManager,
    RateLimiter,
)


class TestDynamicSegmenter(unittest.TestCase):
    def test_initial_chunks_normal(self):
        chunks = DynamicSegmenter.create_initial_chunks(1000, 4)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0].start_byte, 0)
        self.assertEqual(chunks[0].end_byte, 249)
        self.assertEqual(chunks[1].start_byte, 250)
        self.assertEqual(chunks[1].end_byte, 499)
        self.assertEqual(chunks[2].start_byte, 500)
        self.assertEqual(chunks[2].end_byte, 749)
        self.assertEqual(chunks[3].start_byte, 750)
        self.assertEqual(chunks[3].end_byte, 999)

    def test_initial_chunks_small_file(self):
        # File smaller than number of connections
        chunks = DynamicSegmenter.create_initial_chunks(3, 8)
        self.assertLessEqual(len(chunks), 3)
        self.assertEqual(chunks[-1].end_byte, 2)

    def test_initial_chunks_zero_bytes(self):
        chunks = DynamicSegmenter.create_initial_chunks(0, 8)
        self.assertEqual(len(chunks), 1)

    def test_dynamic_subdivision(self):
        task = DownloadTask(
            url="https://example.com/test.iso",
            filename="test.iso",
            total_bytes=10 * 1024 * 1024,
            supports_ranges=True,
            num_connections=2,
        )
        task.chunks = [
            Chunk(id=0, start_byte=0, end_byte=5 * 1024 * 1024 - 1, current_byte=5 * 1024 * 1024 - 1, status="completed"),
            Chunk(id=1, start_byte=5 * 1024 * 1024, end_byte=10 * 1024 * 1024 - 1, current_byte=5 * 1024 * 1024, status="downloading"),
        ]

        # Split largest running chunk (Chunk 1 has ~5MB remaining > 1MB threshold)
        new_chunk = DynamicSegmenter.split_largest_chunk_unlocked(task)
        self.assertIsNotNone(new_chunk)
        self.assertEqual(len(task.chunks), 3)
        self.assertEqual(new_chunk.id, 2)
        self.assertEqual(new_chunk.end_byte, 10 * 1024 * 1024 - 1)
        self.assertEqual(task.chunks[1].end_byte + 1, new_chunk.start_byte)


class TestStorageManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(os.getcwd(), "tests_temp_downloads")
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_save_and_load_meta(self):
        save_path = os.path.join(self.test_dir, "sample.zip")
        task = DownloadTask(
            url="https://example.com/sample.zip",
            filename="sample.zip",
            save_path=save_path,
            total_bytes=5000,
            supports_ranges=True,
            num_connections=2,
            category="compressed",
        )
        task.chunks = [
            Chunk(id=0, start_byte=0, end_byte=2499, current_byte=1000, status="idle"),
            Chunk(id=1, start_byte=2500, end_byte=4999, current_byte=2500, status="idle"),
        ]

        StorageManager.save_task_meta(task)
        meta_path = StorageManager.get_meta_path(save_path)
        self.assertTrue(os.path.exists(meta_path))

        loaded = StorageManager.load_task_meta(meta_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.filename, "sample.zip")
        self.assertEqual(loaded.total_bytes, 5000)
        self.assertEqual(len(loaded.chunks), 2)
        self.assertEqual(loaded.chunks[0].current_byte, 1000)

    def test_preallocate_file(self):
        test_file = os.path.join(self.test_dir, "sparse.dat")
        StorageManager.preallocate_file(test_file, 1024 * 1024)
        self.assertTrue(os.path.exists(test_file))
        self.assertEqual(os.path.getsize(test_file), 1024 * 1024)

    def test_category_folders_routing(self):
        video_dir = StorageManager.get_category_dir(self.test_dir, "video")
        self.assertEqual(os.path.basename(video_dir), "Videos")
        self.assertTrue(os.path.exists(video_dir))

        prog_dir = StorageManager.get_category_dir(self.test_dir, "program")
        self.assertEqual(os.path.basename(prog_dir), "Programs")
        self.assertTrue(os.path.exists(prog_dir))

        music_dir = StorageManager.get_category_dir(self.test_dir, "audio")
        self.assertEqual(os.path.basename(music_dir), "Music")
        self.assertTrue(os.path.exists(music_dir))

    def test_recursive_meta_scanning(self):
        video_dir = StorageManager.get_category_dir(self.test_dir, "video")
        sub_task = DownloadTask(
            url="https://example.com/clip.mp4",
            filename="clip.mp4",
            save_path=os.path.join(video_dir, "clip.mp4"),
            total_bytes=1000,
            category="video",
        )
        StorageManager.save_task_meta(sub_task)

        tasks = StorageManager.scan_and_load_tasks(self.test_dir)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].filename, "clip.mp4")

    def test_extract_archive(self):
        import zipfile
        zip_path = os.path.join(self.test_dir, "test_pkg.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "Hello OpenHydro")

        extracted_dir = StorageManager.extract_archive(zip_path)
        self.assertTrue(os.path.exists(extracted_dir))
        self.assertTrue(os.path.exists(os.path.join(extracted_dir, "hello.txt")))


class TestTaskModel(unittest.TestCase):
    def test_category_determination(self):
        task1 = DownloadTask(url="", filename="movie.mp4")
        self.assertEqual(task1.determine_category(), "video")

        task2 = DownloadTask(url="", filename="song.mp3")
        self.assertEqual(task2.determine_category(), "audio")

        task3 = DownloadTask(url="", filename="archive.7z")
        self.assertEqual(task3.determine_category(), "compressed")

        task4 = DownloadTask(url="", filename="report.pdf")
        self.assertEqual(task4.determine_category(), "document")

        task5 = DownloadTask(url="", filename="setup.exe")
        self.assertEqual(task5.determine_category(), "program")

    def test_progress_percent_capped_at_100(self):
        task = DownloadTask(url="", filename="doc.txt", total_bytes=100)
        task.chunks = [Chunk(id=0, start_byte=0, end_byte=99, current_byte=150, status="completed")]
        self.assertLessEqual(task.progress_percent, 100.0)
        self.assertEqual(task.progress_percent, 100.0)


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_unlimited_rate(self):
        limiter = RateLimiter(0)  # Unlimited
        await limiter.acquire(1024 * 1024)  # Should return immediately

    async def test_set_limit(self):
        limiter = RateLimiter(500 * 1024)
        self.assertEqual(limiter.max_bytes_per_second, 500 * 1024)
        limiter.set_limit(1024 * 1024)
        self.assertEqual(limiter.max_bytes_per_second, 1024 * 1024)


class TestHLSParsingAndAssembly(unittest.TestCase):
    def test_parse_master_playlist(self):
        from openhydro.core import HLSStreamParser
        sample_master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION=1280x720
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2560000,RESOLUTION=1920x1080
1080p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480
480p/index.m3u8
"""
        res = HLSStreamParser.parse_content(sample_master, "https://cdn.example.com/stream/master.m3u8")
        self.assertEqual(res["type"], "master")
        self.assertEqual(len(res["qualities"]), 3)
        # Should be sorted by bandwidth descending (1080p first)
        self.assertEqual(res["qualities"][0]["resolution"], "1920x1080")
        self.assertEqual(res["qualities"][0]["url"], "https://cdn.example.com/stream/1080p/index.m3u8")

    def test_parse_media_playlist(self):
        from openhydro.core import HLSStreamParser
        sample_media = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:9.009,
segment_0.ts
#EXTINF:8.5,
segment_1.ts
#EXT-X-ENDLIST
"""
        res = HLSStreamParser.parse_content(sample_media, "https://cdn.example.com/stream/index.m3u8")
        self.assertEqual(res["type"], "media")
        self.assertEqual(res["total_segments"], 2)
        self.assertEqual(res["segments"][0]["url"], "https://cdn.example.com/stream/segment_0.ts")

    def test_assemble_stream_direct(self):
        from openhydro.core import HLSDownloader
        test_dir = os.path.join(os.getcwd(), "tests_temp_hls")
        os.makedirs(test_dir, exist_ok=True)
        try:
            seg1 = os.path.join(test_dir, "s1.ts")
            seg2 = os.path.join(test_dir, "s2.ts")
            out = os.path.join(test_dir, "output.ts")
            with open(seg1, "wb") as f: f.write(b"PART1")
            with open(seg2, "wb") as f: f.write(b"PART2")

            success = HLSDownloader.assemble_stream([seg1, seg2], out)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(out))
            with open(out, "rb") as f:
                self.assertEqual(f.read(), b"PART1PART2")
        finally:
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)

    def test_video_extractor_url_and_formats(self):
        from openhydro.core.extractor import VideoExtractor
        self.assertTrue(VideoExtractor.is_video_site("https://www.youtube.com/watch?v=UimdbFFMzks"))
        self.assertTrue(VideoExtractor.is_video_site("https://vm.tiktok.com/ZM8abc/"))
        self.assertTrue(VideoExtractor.is_video_site("https://www.facebook.com/watch/?v=12345"))
        self.assertFalse(VideoExtractor.is_video_site("https://example.com/file.zip"))
        
        yt_id = VideoExtractor.extract_youtube_video_id("https://www.youtube.com/watch?v=UimdbFFMzks")
        self.assertEqual(yt_id, "UimdbFFMzks")
        short_id = VideoExtractor.extract_youtube_video_id("https://youtu.be/UimdbFFMzks?t=10")
        self.assertEqual(short_id, "UimdbFFMzks")

        # Test Playlist URL detection
        self.assertTrue(VideoExtractor.is_playlist_url("https://www.youtube.com/playlist?list=PLr6-GrHUlVf_ZNmuQSXdS197Oyr1L9sPB"))
        self.assertTrue(VideoExtractor.is_playlist_url("https://www.youtube.com/watch?v=123&list=PLr6-GrHUlVf_ZNmuQSXdS197Oyr1L9sPB"))
        self.assertFalse(VideoExtractor.is_playlist_url("https://www.youtube.com/watch?v=123"))

        # Test Google Drive detection
        from openhydro.core.gdrive import GoogleDriveHandler
        gfolder = "https://drive.google.com/drive/folders/1dQ4sx0-__Nvg65rxTSgQrl7VyW_FZ9QI?usp=sharing"
        gfile = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view?usp=sharing"
        self.assertTrue(GoogleDriveHandler.is_gdrive_folder(gfolder))
        self.assertEqual(GoogleDriveHandler.extract_folder_id(gfolder), "1dQ4sx0-__Nvg65rxTSgQrl7VyW_FZ9QI")
        self.assertTrue(GoogleDriveHandler.is_gdrive_file(gfile))
        self.assertEqual(GoogleDriveHandler.extract_file_id(gfile), "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs")
        self.assertTrue(VideoExtractor.is_playlist_url(gfolder))

        # Test Batch multi-link extraction
        batch_text = f"{gfile}\nhttps://drive.google.com/file/d/1eE-5jm2G8N5FL4HHeexrL0-Lg15bc0-D/view"
        pairs = GoogleDriveHandler.find_all_file_ids(batch_text)
        self.assertEqual(len(pairs), 2)
        self.assertTrue(VideoExtractor.is_playlist_url(batch_text))

        # Test Updater version comparison
        from openhydro.core.updater import parse_version, is_newer_version
        self.assertEqual(parse_version("v1.1.2"), (1, 1, 2))
        self.assertEqual(parse_version("1.0.0"), (1, 0, 0))
        self.assertTrue(is_newer_version("1.2.0", "1.1.2"))
        self.assertTrue(is_newer_version("2.0.0", "1.9.9"))
        self.assertFalse(is_newer_version("1.1.2", "1.1.2"))
        self.assertFalse(is_newer_version("1.0.0", "1.1.2"))

    def test_torrent_magnet_parsing(self):
        from openhydro.core.torrent import TorrentManager
        from openhydro.core.task import DownloadTask

        magnet = "magnet:?xt=urn:btih:d6b6e4e427181c4e7c7a5223cf20a2e576082e66&dn=Ubuntu+22.04+Desktop&tr=udp%3A%2F%2Ftracker.openbittorrent.com%3A80"
        info = TorrentManager.parse_magnet_info(magnet)
        self.assertEqual(info["name"], "Ubuntu 22.04 Desktop")
        self.assertEqual(info["info_hash"], "d6b6e4e427181c4e7c7a5223cf20a2e576082e66")
        self.assertEqual(len(info["trackers"]), 1)

        task = DownloadTask(url=magnet, filename="Ubuntu.torrent", save_path="ubuntu.torrent", is_torrent=True)
        self.assertEqual(task.determine_category(), "torrent")

    def test_torrent_binary_available(self):
        from openhydro.core.torrent import TorrentManager
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mgr = TorrentManager(td)
            self.assertTrue(mgr.is_available())

    def test_storage_metadata_persistence(self):
        from openhydro.core.storage import StorageManager
        from openhydro.core.task import DownloadTask, TaskStatus
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "test_video.mp4")
            task = DownloadTask(
                url="https://youtube.com/watch?v=dummy",
                filename="test_video.mp4",
                save_path=target,
                is_extractor=True,
                selected_format="audio_mp3",
                is_torrent=False,
            )
            StorageManager.save_task_meta(task)
            meta_path = StorageManager.get_meta_path(target)
            self.assertTrue(os.path.exists(meta_path))

            loaded = StorageManager.load_task_meta(meta_path)
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.is_extractor)
            self.assertEqual(loaded.selected_format, "audio_mp3")

    def test_probe_magnet_url(self):
        from openhydro.core.engine import DownloadEngine
        import asyncio
        magnet = "magnet:?xt=urn:btih:d6b6e4e427181c4e7c7a5223cf20a2e576082e66&dn=ArchLinux&tr=udp%3A%2F%2Ftracker.archlinux.org%3A6969"
        info = asyncio.run(DownloadEngine.probe_url(magnet))
        self.assertEqual(info["filename"], "ArchLinux")
        self.assertTrue(info["is_torrent"])
        self.assertEqual(info["category"], "torrent")


if __name__ == "__main__":
    unittest.main()

