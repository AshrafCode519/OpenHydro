"""
Queue, scheduler, auto-shutdown and task lifecycle tests for OpenHydro
Downloads are replaced by a fake that idles until cancelled, so no network is involved.
The real system shutdown is patched out for the whole module.
"""

import os
import shutil
import asyncio
import tempfile
import threading
import unittest
from datetime import datetime
from unittest import mock

from starlette.testclient import TestClient

from helpers import make_engine
from openhydro.core import DownloadEngine, DownloadTask, TaskStatus, StorageManager
from openhydro.api import create_app


def setUpModule():
    # Never let a test power off the machine
    patcher = mock.patch.object(DownloadEngine, "trigger_system_shutdown")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


async def never_returns(*args, **kwargs):
    await asyncio.Event().wait()


async def idle_download(task):
    """Stands in for _execute_download: 'downloads' until cancelled, like the real loop."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        if task.status == TaskStatus.DOWNLOADING:
            task.status = TaskStatus.PAUSED


class LifecycleTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        DownloadEngine.trigger_system_shutdown.reset_mock()
        self.download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.download_dir, ignore_errors=True)
        self.engine = make_engine(self, self.download_dir)
        self.engine._execute_download = idle_download
        self.engine.scheduler.set_max_active(2)
        self.probe = mock.patch.object(
            self.engine, "probe_url",
            mock.AsyncMock(return_value={"filename": "file.bin", "total_bytes": 0, "supports_ranges": False}),
        )
        self.probe.start()

    async def asyncTearDown(self):
        self.probe.stop()
        await self.engine.pause_all()

    async def add(self, n: int, auto_start: bool = True):
        return [await self.engine.add_download(f"https://example.com/{i}.bin", filename=f"f{i}.bin", auto_start=auto_start)
                for i in range(n)]

    def statuses(self, tasks):
        return sorted(t.status.value for t in tasks)

    def finish(self, task):
        task.status = TaskStatus.COMPLETED
        self.engine._running_loops[task.id].cancel()


class TestQueue(LifecycleTestCase):
    async def test_auto_start_respects_max_active(self):
        tasks = await self.add(4)
        self.assertEqual(self.statuses(tasks), ["downloading", "downloading", "queued", "queued"])

    async def test_scheduler_starts_queued_when_slot_frees(self):
        tasks = await self.add(4)
        self.finish(tasks[0])
        await self.engine.scheduler._tick()
        self.assertEqual(tasks[2].status, TaskStatus.DOWNLOADING, "oldest queued task should start first")
        self.assertEqual(tasks[3].status, TaskStatus.QUEUED)

    async def test_paused_queued_task_is_not_started(self):
        tasks = await self.add(3)
        await self.engine.pause_task(tasks[2].id)
        self.finish(tasks[0])
        await self.engine.scheduler._tick()
        self.assertEqual(tasks[2].status, TaskStatus.PAUSED)

    async def test_resume_all_respects_limit(self):
        tasks = await self.add(4, auto_start=False)
        for t in tasks:
            t.status = TaskStatus.PAUSED
        await self.engine.resume_all()
        self.assertEqual(self.statuses(tasks), ["downloading", "downloading", "queued", "queued"])

    async def test_manual_resume_starts_immediately(self):
        tasks = await self.add(3)
        await self.engine.pause_task(tasks[2].id)
        await self.engine.resume_task(tasks[2].id)
        self.assertEqual(tasks[2].status, TaskStatus.DOWNLOADING)

    async def test_scheduled_start_fires_once_per_day(self):
        tasks = await self.add(3, auto_start=False)
        for t in tasks:
            t.status = TaskStatus.PAUSED
        self.engine.scheduler.set_schedule_start("02:00")

        await self.engine.scheduler._tick(now=datetime(2026, 11, 3, 1, 59))
        self.assertEqual(self.statuses(tasks), ["paused"] * 3)

        await self.engine.scheduler._tick(now=datetime(2026, 11, 3, 2, 0))
        self.assertEqual(self.statuses(tasks), ["downloading", "downloading", "queued"])

        # A task the user pauses again during that minute stays paused
        await self.engine.pause_task(tasks[0].id)
        await self.engine.scheduler._tick(now=datetime(2026, 11, 3, 2, 0))
        self.assertEqual(tasks[0].status, TaskStatus.PAUSED)


class TestAutoShutdown(LifecycleTestCase):
    async def test_finished_history_alone_never_shuts_down(self):
        tasks = await self.add(2, auto_start=False)
        for t in tasks:
            t.status = TaskStatus.COMPLETED
        self.engine.scheduler.set_auto_shutdown(True)
        for _ in range(3):
            await self.engine.scheduler._tick()
        self.engine.trigger_system_shutdown.assert_not_called()

    async def test_shuts_down_once_after_downloads_finish(self):
        tasks = await self.add(3)
        self.engine.scheduler.set_auto_shutdown(True)

        self.finish(tasks[0])
        self.finish(tasks[1])
        await self.engine.scheduler._tick()  # starts the queued third task
        self.engine.trigger_system_shutdown.assert_not_called()

        self.finish(tasks[2])
        await self.engine.scheduler._tick()
        await self.engine.scheduler._tick()
        self.engine.trigger_system_shutdown.assert_called_once()
        self.assertFalse(self.engine.scheduler.shutdown_on_complete)

    async def test_activity_after_enabling_arms_shutdown(self):
        self.engine.scheduler.set_auto_shutdown(True)
        await self.engine.scheduler._tick()
        tasks = await self.add(1)
        await self.engine.scheduler._tick()
        self.finish(tasks[0])
        await self.engine.scheduler._tick()
        self.engine.trigger_system_shutdown.assert_called_once()


class TestCrossThreadControl(unittest.TestCase):
    def test_submit_runs_on_the_engine_loop(self):
        download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, download_dir, ignore_errors=True)
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            engine = make_engine(self, download_dir)
            engine._execute_download = idle_download
            task = DownloadTask(url="https://example.com/a.bin", filename="a.bin",
                                save_path=os.path.join(download_dir, "a.bin"), status=TaskStatus.PAUSED)
            engine.tasks[task.id] = task

            with self.assertRaises(RuntimeError):
                engine.submit(engine.resume_all())  # no loop attached yet

            engine.attach_loop(loop)
            engine.submit(engine.resume_all()).result(timeout=5)
            self.assertEqual(task.status, TaskStatus.DOWNLOADING)
            loop_task = engine._running_loops[task.id]
            self.assertIs(loop_task.get_loop(), loop)
            self.assertFalse(loop_task.done(), "download must keep running after the call returns")

            engine.submit(engine.pause_all()).result(timeout=5)
            self.assertEqual(task.status, TaskStatus.PAUSED)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)


class TestCancelKeepsCancelled(LifecycleTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        del self.engine._execute_download  # use the real dispatcher for these

    async def run_and_cancel(self, task):
        self.engine.tasks[task.id] = task
        await self.engine.start_task(task.id)
        await asyncio.sleep(0.05)
        await self.engine.cancel_task(task.id)
        self.assertEqual(task.status, TaskStatus.CANCELLED)

    async def test_cancel_video_platform_download(self):
        with mock.patch("openhydro.core.engine.VideoExtractor.download", new=never_returns):
            await self.run_and_cancel(DownloadTask(url="https://www.youtube.com/watch?v=x", filename="v.mp4",
                                                   save_path=os.path.join(self.download_dir, "v.mp4"), is_extractor=True))

    async def test_cancel_hls_download(self):
        with mock.patch("openhydro.core.engine.HLSStreamParser.fetch_and_parse", new=never_returns):
            await self.run_and_cancel(DownloadTask(url="https://example.com/s.m3u8", filename="s.mp4",
                                                   save_path=os.path.join(self.download_dir, "s.mp4"), is_hls=True))


class TestRemoveTask(unittest.TestCase):
    def setUp(self):
        self.download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.download_dir, ignore_errors=True)
        self.engine = make_engine(self, self.download_dir)
        self.client = TestClient(create_app(self.engine, remote_token="t"),
                                 base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def make_task(self, name: str, completed: bool) -> DownloadTask:
        path = os.path.join(self.download_dir, name)
        task = DownloadTask(url=f"https://example.com/{name}", filename=name, save_path=path,
                            status=TaskStatus.COMPLETED if completed else TaskStatus.PAUSED)
        with open(path if completed else StorageManager.get_part_path(path), "wb") as f:
            f.write(b"data")
        self.engine.store.save(task)
        self.engine.tasks[task.id] = task
        return task

    def test_remove_from_list_keeps_file_and_does_not_come_back(self):
        task = self.make_task("movie.mp4", completed=True)
        resp = self.client.delete(f"/api/tasks/{task.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(task.id, self.engine.tasks)
        self.assertTrue(os.path.exists(task.save_path), "the downloaded file must be kept")
        restored = make_engine(self, self.download_dir)
        self.assertNotIn(task.id, restored.tasks, "removed task came back after restart")

    def test_remove_with_delete_file_deletes_it(self):
        task = self.make_task("movie.mp4", completed=True)
        self.client.delete(f"/api/tasks/{task.id}?delete_file=true")
        self.assertFalse(os.path.exists(task.save_path))

    def test_remove_unfinished_cleans_temporary_files(self):
        task = self.make_task("big.iso", completed=False)
        self.client.delete(f"/api/tasks/{task.id}")
        self.assertFalse(os.path.exists(StorageManager.get_part_path(task.save_path)))
        self.assertNotIn(task.id, [t.id for t in self.engine.store.load_all()])


if __name__ == "__main__":
    unittest.main()
