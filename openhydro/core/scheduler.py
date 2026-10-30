"""
OpenHydro Smart Scheduler & Queue Manager
Controls concurrent queue processing, time-based scheduled starts, and auto-shutdown.
"""

import time
import os
import asyncio
import threading
from datetime import datetime
from typing import Optional, List, Dict, Callable
from .task import DownloadTask, TaskStatus


class SmartScheduler:
    def __init__(self, engine, max_active_downloads: int = 3):
        self.engine = engine
        self.max_active_downloads = max_active_downloads
        self.is_running = False
        self._loop_task: Optional[asyncio.Task] = None
        self.shutdown_on_complete = False
        self.scheduled_time: Optional[str] = None  # Format "HH:MM"
        self._lock = threading.Lock()

    def set_max_active(self, count: int):
        with self._lock:
            self.max_active_downloads = max(1, count)

    def set_schedule_start(self, time_str: Optional[str]):
        """Sets target start time in HH:MM format (24-hour). None to disable."""
        with self._lock:
            self.scheduled_time = time_str

    def set_auto_shutdown(self, enabled: bool):
        with self._lock:
            self.shutdown_on_complete = enabled

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
                now_str = datetime.now().strftime("%H:%M")

                # 1. Check if scheduled start time matches
                if self.scheduled_time and now_str == self.scheduled_time:
                    # Unpause/start queued items
                    for t in list(self.engine.tasks.values()):
                        if t.status in (TaskStatus.QUEUED, TaskStatus.PAUSED):
                            await self.engine.start_task(t.id)

                # 2. Queue management: maintain max_active_downloads
                active_count = sum(1 for t in self.engine.tasks.values() if t.status == TaskStatus.DOWNLOADING)

                if active_count < self.max_active_downloads:
                    slots_free = self.max_active_downloads - active_count
                    # Find queued tasks
                    for t in list(self.engine.tasks.values()):
                        if slots_free <= 0:
                            break
                        if t.status == TaskStatus.QUEUED:
                            await self.engine.start_task(t.id)
                            slots_free -= 1

                # 3. Check auto-shutdown condition
                if self.shutdown_on_complete:
                    all_tasks = list(self.engine.tasks.values())
                    if all_tasks and all(t.status in (TaskStatus.COMPLETED, TaskStatus.ERROR) for t in all_tasks):
                        print("[Scheduler] All downloads complete! Triggering system shutdown in 30 seconds...")
                        self.shutdown_on_complete = False
                        # On Windows: shutdown /s /t 30
                        if os.name == "nt":
                            os.system("shutdown /s /t 30")

            except Exception as e:
                print(f"[Scheduler] Loop exception: {e}")

            await asyncio.sleep(2.0)

