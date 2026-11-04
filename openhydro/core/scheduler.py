"""
OpenHydro Smart Scheduler & Queue Manager
Controls concurrent queue processing, time-based scheduled starts, and auto-shutdown.
"""

import asyncio
import threading
from datetime import datetime
from typing import Optional
from .task import TaskStatus

BUSY_STATUSES = (TaskStatus.DOWNLOADING, TaskStatus.QUEUED)


class SmartScheduler:
    def __init__(self, engine, max_active_downloads: int = 3):
        self.engine = engine
        self.max_active_downloads = max_active_downloads
        self.is_running = False
        self._loop_task: Optional[asyncio.Task] = None
        self.shutdown_on_complete = False
        # Auto-shutdown only fires after downloads actually ran while it was enabled,
        # never just because the history happens to contain only finished tasks
        self._saw_activity = False
        self.scheduled_time: Optional[str] = None  # Format "HH:MM"
        self._last_scheduled_run: Optional[str] = None  # Date the scheduled start last fired
        self._lock = threading.Lock()

    def set_max_active(self, count: int):
        with self._lock:
            self.max_active_downloads = max(1, count)

    def set_schedule_start(self, time_str: Optional[str]):
        """Sets target start time in HH:MM format (24-hour). None to disable."""
        with self._lock:
            self.scheduled_time = time_str or None

    def set_auto_shutdown(self, enabled: bool):
        with self._lock:
            self.shutdown_on_complete = enabled
            self._saw_activity = enabled and self._any_busy()

    def _any_busy(self) -> bool:
        return any(t.status in BUSY_STATUSES for t in list(self.engine.tasks.values()))

    async def start(self):
        """Starts the queue monitoring loop."""
        if self.is_running:
            return
        self.is_running = True
        self._loop_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        self.is_running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()

    async def _scheduler_loop(self):
        while self.is_running:
            try:
                await self._tick()
            except Exception as e:
                print(f"[Scheduler] Loop exception: {e}")

            await asyncio.sleep(2.0)

    async def _tick(self, now: Optional[datetime] = None):
        now = now or datetime.now()

        # 1. Scheduled start: once a day at HH:MM, put paused downloads back in the queue
        today = now.strftime("%Y-%m-%d")
        if self.scheduled_time and now.strftime("%H:%M") == self.scheduled_time and self._last_scheduled_run != today:
            self._last_scheduled_run = today
            for t in sorted(self.engine.tasks.values(), key=lambda t: t.created_at):
                if t.status == TaskStatus.PAUSED:
                    await self.engine.resume_task(t.id, respect_queue=True)

        # 2. Queue management: start queued tasks (oldest first) while slots are free
        slots_free = self.max_active_downloads - self.engine.active_download_count()
        for t in sorted(self.engine.tasks.values(), key=lambda t: t.created_at):
            if slots_free <= 0:
                break
            if t.status == TaskStatus.QUEUED:
                await self.engine.start_task(t.id)
                slots_free -= 1

        # 3. Auto-shutdown once everything that ran while it was enabled has finished
        if self.shutdown_on_complete:
            if self._any_busy():
                self._saw_activity = True
            elif self._saw_activity:
                print("[Scheduler] All downloads finished: triggering system shutdown")
                self.shutdown_on_complete = False
                self._saw_activity = False
                self.engine.trigger_system_shutdown()
