"""
Event throttling and archive extraction safety tests for OpenHydro
"""

import io
import os
import time
import shutil
import tarfile
import zipfile
import tempfile
import unittest

from helpers import make_engine
from openhydro.core import DownloadTask, StorageManager


class TestProgressThrottling(unittest.TestCase):
    def setUp(self):
        download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, download_dir, ignore_errors=True)
        self.engine = make_engine(self, download_dir)
        self.events = []
        self.engine.register_listener(lambda event, payload: self.events.append(event))
        self.task = DownloadTask(url="https://example.com/a.bin", filename="a.bin",
                                 save_path=os.path.join(download_dir, "a.bin"))

    def test_progress_bursts_are_coalesced(self):
        for _ in range(50):
            self.engine._notify("task_progress", self.task)
        self.assertEqual(self.events.count("task_progress"), 1)

        time.sleep(0.3)
        self.engine._notify("task_progress", self.task)
        self.assertEqual(self.events.count("task_progress"), 2)

    def test_status_changes_are_never_dropped(self):
        self.engine._notify("task_progress", self.task)
        for event in ("task_updated", "task_error", "task_completed", "task_updated"):
            self.engine._notify(event, self.task)
        self.assertEqual(self.events, ["task_progress", "task_updated", "task_error", "task_completed", "task_updated"])

    def test_throttling_is_per_task(self):
        other = DownloadTask(url="https://example.com/b.bin", filename="b.bin", save_path=self.task.save_path + "2")
        self.engine._notify("task_progress", self.task)
        self.engine._notify("task_progress", other)
        self.assertEqual(self.events.count("task_progress"), 2)


class TestArchiveExtraction(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.downloads = os.path.join(self.root, "Downloads")
        os.makedirs(self.downloads)
        self.escaped = os.path.join(self.root, "evil.txt")

    def test_tar_path_traversal_is_refused(self):
        archive = os.path.join(self.downloads, "pack.tar.gz")
        with tarfile.open(archive, "w:gz") as tf:
            for name, data in (("ok.txt", b"fine"), ("../../evil.txt", b"owned")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

        with self.assertRaises(Exception):
            StorageManager.extract_archive(archive)
        self.assertFalse(os.path.exists(self.escaped), "archive wrote outside its folder")

    def test_zip_path_traversal_stays_inside(self):
        archive = os.path.join(self.downloads, "pack.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ok.txt", "fine")
            zf.writestr("../../evil.txt", "owned")

        target = StorageManager.extract_archive(archive)
        self.assertTrue(os.path.exists(os.path.join(target, "ok.txt")))
        self.assertFalse(os.path.exists(self.escaped), "archive wrote outside its folder")

    def test_normal_tar_still_extracts(self):
        archive = os.path.join(self.downloads, "pack.tar")
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo("docs/readme.txt")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"hello"))

        target = StorageManager.extract_archive(archive)
        with open(os.path.join(target, "docs", "readme.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hello")


if __name__ == "__main__":
    unittest.main()
