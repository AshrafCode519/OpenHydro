"""
Task persistence tests for OpenHydro
Covers the central SQLite task store, restart restoration and the legacy .hydro.meta migration.
"""

import os
import glob
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import make_engine
from openhydro.core import DownloadTask, Chunk, TaskStatus, StorageManager


class TaskStoreTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.download_dir, ignore_errors=True)

    def engine(self):
        engine = make_engine(self, self.download_dir)
        probe = mock.patch.object(
            engine, "probe_url",
            mock.AsyncMock(return_value={"filename": "file.bin", "total_bytes": 4096, "supports_ranges": True}),
        )
        probe.start()
        self.addCleanup(probe.stop)
        return engine

    def saved_task(self, engine, **overrides) -> DownloadTask:
        fields = dict(url="https://example.com/file.bin", filename="file.bin",
                      save_path=os.path.join(self.download_dir, "file.bin"))
        fields.update(overrides)
        task = DownloadTask(**fields)
        task.chunks = [Chunk(id=0, start_byte=0, end_byte=4095, current_byte=1024)]
        engine.tasks[task.id] = task
        engine.store.save(task)
        return task


class TestRestore(TaskStoreTestCase):
    async def test_tasks_survive_restart(self):
        task = self.saved_task(self.engine(), status=TaskStatus.PAUSED, referrer="https://example.com/page")
        restored = self.engine().tasks[task.id]
        self.assertEqual(restored.url, task.url)
        self.assertEqual(restored.referrer, "https://example.com/page")
        self.assertEqual(restored.chunks[0].current_byte, 1024)
        self.assertEqual(restored.status, TaskStatus.PAUSED)

    async def test_interrupted_download_comes_back_paused(self):
        task = self.saved_task(self.engine(), status=TaskStatus.DOWNLOADING)
        self.assertEqual(self.engine().tasks[task.id].status, TaskStatus.PAUSED)

    async def test_error_and_message_are_kept(self):
        task = self.saved_task(self.engine(), status=TaskStatus.ERROR, error_message="HTTP 403")
        restored = self.engine().tasks[task.id]
        self.assertEqual(restored.status, TaskStatus.ERROR)
        self.assertEqual(restored.error_message, "HTTP 403")

    async def test_unfinished_torrent_is_not_marked_completed(self):
        # aria2 writes straight to the final path, so the file exists long before it is complete
        path = os.path.join(self.download_dir, "ubuntu.iso")
        with open(path, "wb") as f:
            f.write(b"partial")
        task = self.saved_task(self.engine(), url="magnet:?xt=urn:btih:abc", filename="ubuntu.iso",
                               save_path=path, is_torrent=True, status=TaskStatus.DOWNLOADING)
        self.assertEqual(self.engine().tasks[task.id].status, TaskStatus.PAUSED)

    async def test_saved_category_is_kept(self):
        task = self.saved_task(self.engine(), filename="talk.srt",
                               save_path=os.path.join(self.download_dir, "talk.srt"), category="document")
        self.assertEqual(self.engine().tasks[task.id].category, "document")

    async def test_renamed_torrent_is_not_duplicated(self):
        engine = self.engine()
        task = self.saved_task(engine, url="magnet:?xt=urn:btih:abc", filename="Torrent_Download", is_torrent=True)
        task.filename = "Real Name"
        task.save_path = os.path.join(self.download_dir, "Real Name")
        engine.store.save(task)
        restored = [t for t in self.engine().tasks.values()]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].filename, "Real Name")

    async def test_no_sidecar_files_in_downloads_folder(self):
        await self.engine().add_download("https://example.com/file.bin", auto_start=False)
        self.assertEqual(glob.glob(os.path.join(self.download_dir, "**", "*.hydro.meta"), recursive=True), [])


class TestLegacyMigration(TaskStoreTestCase):
    def write_legacy_meta(self, subfolder: str, name: str, status: TaskStatus) -> DownloadTask:
        folder = os.path.join(self.download_dir, subfolder)
        os.makedirs(folder, exist_ok=True)
        task = DownloadTask(url=f"https://example.com/{name}", filename=name,
                            save_path=os.path.join(folder, name), status=status)
        StorageManager.save_task_meta(task)
        return task

    async def test_legacy_meta_files_are_imported_once_and_removed(self):
        paused = self.write_legacy_meta("Videos", "clip.mp4", TaskStatus.PAUSED)
        done = self.write_legacy_meta("Programs", "setup.exe", TaskStatus.COMPLETED)

        first = self.engine()
        self.assertEqual(first.tasks[paused.id].status, TaskStatus.PAUSED)
        self.assertEqual(first.tasks[done.id].status, TaskStatus.COMPLETED)
        self.assertEqual(glob.glob(os.path.join(self.download_dir, "**", "*.hydro.meta"), recursive=True), [])

        second = self.engine()
        self.assertEqual(set(second.tasks), {paused.id, done.id})

    async def test_scan_runs_only_once(self):
        self.engine()
        late = self.write_legacy_meta("Music", "song.mp3", TaskStatus.PAUSED)
        self.assertNotIn(late.id, self.engine().tasks, "the downloads folder should not be rescanned on every start")


class TestDuplicateNames(TaskStoreTestCase):
    async def test_same_link_twice_gets_separate_files(self):
        engine = self.engine()
        first = await engine.add_download("https://example.com/file.bin", auto_start=False)
        second = await engine.add_download("https://example.com/file.bin", auto_start=False)
        self.assertNotEqual(first.save_path, second.save_path)
        self.assertNotEqual(StorageManager.get_part_path(first.save_path), StorageManager.get_part_path(second.save_path))

    async def test_existing_partial_file_is_not_reused(self):
        path = os.path.join(self.download_dir, "file.bin")
        with open(StorageManager.get_part_path(path), "wb") as f:
            f.write(b"someone else's partial data")
        task = await self.engine().add_download("https://example.com/file.bin", auto_start=False)
        self.assertNotEqual(task.save_path, path)

    async def test_completed_history_does_not_block_the_name(self):
        engine = self.engine()
        self.saved_task(engine, status=TaskStatus.COMPLETED)  # its file was deleted by the user since
        task = await engine.add_download("https://example.com/file.bin", auto_start=False)
        self.assertEqual(os.path.basename(task.save_path), "file.bin")


if __name__ == "__main__":
    unittest.main()
