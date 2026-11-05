"""
OpenHydro Core Download Manager Engine
Orchestrates downloads, queues, dynamic segmentation, state restoration, and task lifecycles.
"""

import os
import re
import sys
import time
import shutil
import asyncio
import threading
from urllib.parse import urlparse, unquote
from typing import Dict, List, Optional, Callable, Any
import httpx

from .task import DownloadTask, Chunk, TaskStatus
from .segmenter import DynamicSegmenter
from .storage import StorageManager, TaskStore
from .limiter import RateLimiter
from .worker import ChunkWorker, DEFAULT_USER_AGENT, MAX_RETRIES, RETRY_BACKOFF_BASE, shared_ssl_context, shared_ssl_context_async
from .hls import HLSStreamParser, HLSDownloader
from .scheduler import SmartScheduler
from .extractor import VideoExtractor
from .torrent import TorrentManager
from .security import sanitize_filename, get_app_data_dir
from .settings import AppSettings
from . import ffmpeg_component

# Minimum seconds between two progress events for the same task (status changes are immediate)
PROGRESS_EVENT_INTERVAL = 0.25
# Seconds between saves of a running download's progress (its resume point) to the task store
PROGRESS_SAVE_INTERVAL = 2.0

# Retrying these will not help (expired/forbidden link, missing file): give up after one more try
PERMANENT_HTTP_ERRORS = {400, 401, 403, 404, 405, 410, 416, 451}
PERMANENT_ERROR_RETRIES = 1


class DownloadEngine:
    def __init__(self, download_dir: Optional[str] = None, store_path: Optional[str] = None):
        self.store = TaskStore(store_path or os.path.join(get_app_data_dir(), "tasks.db"))
        self.settings = AppSettings(self.store)
        # An explicit folder (tests, scripts) wins over the user's setting
        self.download_dir = download_dir or self.settings.get("download_dir") or StorageManager.get_default_download_dir()
        self.tasks: Dict[str, DownloadTask] = {}
        self.rate_limiter = RateLimiter(self.settings.get("speed_limit_kbps") * 1024)
        self._running_loops: Dict[str, asyncio.Task] = {}
        self._listeners: List[Callable] = []
        self._last_progress_event: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.scheduler = SmartScheduler(self, max_active_downloads=self.settings.get("max_active_downloads"))
        self.torrent_manager = TorrentManager(self.download_dir)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Load the CA bundle now, off the event loop: on a busy machine it takes seconds and
        # would otherwise delay the first download (or probe) after every start
        threading.Thread(target=shared_ssl_context, name="ssl-warmup", daemon=True).start()

        # Restore saved tasks from disk
        self._restore_persisted_tasks()

    def attach_loop(self, loop: asyncio.AbstractEventLoop):
        """Remembers the server event loop that owns every download coroutine."""
        self._loop = loop

    def submit(self, coro):
        """
        Runs an engine coroutine on the server loop from another thread (tray menu, GUI).
        asyncio.run() there would start downloads on a throwaway loop that is closed immediately.
        """
        if not self._loop or not self._loop.is_running():
            coro.close()
            raise RuntimeError("Engine event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _restore_persisted_tasks(self):
        """Restores interrupted/completed tasks from the task store on startup."""
        try:
            self._import_legacy_meta_files()
            auto_resume = self.settings.get("auto_resume")
            with self._lock:
                for task in self.store.load_all():
                    # Interrupted downloads come back queued; the scheduler restarts them
                    if task.status == TaskStatus.QUEUED and not auto_resume:
                        task.status = TaskStatus.PAUSED
                    self.tasks[task.id] = task
        except Exception as e:
            print(f"[Engine] Error restoring tasks: {e}")

    def _import_legacy_meta_files(self):
        """One-time move of the old .hydro.meta sidecar files into the task store."""
        marker = "legacy_meta_imported:" + os.path.normcase(os.path.abspath(self.download_dir))
        if self.store.get_setting(marker):
            return
        for meta_path, task in StorageManager.scan_legacy_meta(self.download_dir):
            self.store.save(task)
            try:
                os.remove(meta_path)
            except OSError as e:
                print(f"[Engine] Could not remove legacy metadata {meta_path}: {e}")
        self.store.set_setting(marker, "1")

    def close(self):
        """Releases the task store (call on shutdown after the final saves)."""
        self.store.close()

    def register_listener(self, callback: Callable):
        self._listeners.append(callback)

    def _notify(self, event_type: str, task: Any):
        if event_type == "task_progress" and isinstance(task, DownloadTask):
            # Every worker reports progress; with 32 connections that was dozens of full task
            # snapshots per second. Status changes (updated/completed/error) are never throttled.
            now = time.monotonic()
            if now - self._last_progress_event.get(task.id, 0.0) < PROGRESS_EVENT_INTERVAL:
                return
            self._last_progress_event[task.id] = now
        payload = task.to_dict() if hasattr(task, "to_dict") else task
        for listener in self._listeners:
            try:
                listener(event_type, payload)
            except Exception as e:
                print(f"[Engine] Listener error: {e}")

    def trigger_system_shutdown(self):
        import subprocess
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["shutdown", "/s", "/t", "60", "/c", "OpenHydro: اكتملت كافة التحميلات. سيتم إيقاف تشغيل الكمبيوتر خلال 60 ثانية."])
            elif sys.platform.startswith("darwin") or sys.platform.startswith("linux"):
                subprocess.Popen(["shutdown", "-h", "+1", "OpenHydro: downloads completed"])
            self._notify("auto_shutdown_triggered", {"countdown": 60})
        except Exception as e:
            print(f"[AutoShutdown] Error: {e}")

    @staticmethod
    def cancel_system_shutdown() -> bool:
        import subprocess
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["shutdown", "/a"], capture_output=True)
                return True
            else:
                subprocess.run(["shutdown", "-c"], capture_output=True)
                return True
        except Exception:
            return False

    @staticmethod
    async def probe_url(url: str, custom_headers: Optional[Dict[str, str]] = None) -> Dict:
        """
        Probes the URL to determine filename, size, and range support.
        """
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            **(custom_headers or {}),
        }
        filename = "download.bin"
        total_bytes = 0
        supports_ranges = False

        # Try to infer filename from URL path first
        parsed = urlparse(url)
        path_name = os.path.basename(unquote(parsed.path))
        if path_name and "." in path_name:
            filename = path_name

        # Check if Magnet link
        if url.startswith("magnet:"):
            from .torrent import TorrentManager
            minfo = TorrentManager.parse_magnet_info(url)
            t_name = minfo["name"] or "Torrent_Download"
            return {
                "filename": t_name,
                "total_bytes": minfo.get("total_size", 0),
                "supports_ranges": True,
                "is_torrent": True,
                "category": "torrent",
                "title": t_name,
            }

        # Check if URL belongs to a playlist (YouTube, SoundCloud sets, Google Drive folder, etc.)
        from .gdrive import GoogleDriveHandler
        if VideoExtractor.is_playlist_url(url) or GoogleDriveHandler.is_gdrive_folder(url) or len(GoogleDriveHandler.find_all_file_ids(url)) > 1:
            try:
                pinfo = await VideoExtractor.extract_playlist(url)
                return {
                    "filename": f"{pinfo['title_sanitized']}",
                    "total_bytes": 0,
                    "supports_ranges": False,
                    "is_playlist": True,
                    "is_video_platform": True,
                    "is_gdrive": pinfo.get("is_gdrive", False),
                    "title": pinfo["title"],
                    "uploader": pinfo.get("uploader"),
                    "count": pinfo["count"],
                    "entries": pinfo["entries"],
                }
            except Exception as e:
                print(f"[Probe] Playlist extraction failed: {e}")

        # Check if single Google Drive file
        if GoogleDriveHandler.is_gdrive_file(url):
            try:
                ginfo = await GoogleDriveHandler.resolve_direct_download(url)
                return {
                    "filename": ginfo["filename"],
                    "total_bytes": ginfo["total_bytes"],
                    "supports_ranges": ginfo["supports_ranges"],
                    "is_gdrive": True,
                    "resolved_url": ginfo["resolved_url"],
                }
            except Exception as e:
                print(f"[Probe] Google Drive file resolve failed: {e}")

        # Check if URL belongs to a video streaming platform (YouTube, TikTok, Facebook, etc.)
        if VideoExtractor.is_video_site(url):
            try:
                vinfo = await VideoExtractor.extract_info(url)
                return {
                    "filename": vinfo["filename"],
                    "total_bytes": vinfo.get("total_bytes", 0),
                    "supports_ranges": True,
                    "is_video_platform": True,
                    "title": vinfo["title"],
                    "thumbnail": vinfo.get("thumbnail"),
                    "duration": vinfo.get("duration", 0),
                    "qualities": vinfo.get("qualities", []),
                    "has_subtitles": vinfo.get("has_subtitles", False),
                }
            except Exception as e:
                print(f"[Probe] Platform video extraction failed: {e}")

        # Check if URL is an HLS playlist
        if HLSStreamParser.is_m3u8_url(url):
            try:
                hls_data = await HLSStreamParser.fetch_and_parse(url, headers)
                base_name = os.path.splitext(filename)[0] or "video_stream"
                if not base_name.endswith(".mp4"):
                    base_name += ".mp4"
                return {
                    "filename": base_name,
                    "total_bytes": 0,
                    "supports_ranges": False,
                    "is_hls": True,
                    "hls_info": hls_data,
                }
            except Exception as e:
                print(f"[Probe] HLS parse failed: {e}")

        try:
            timeout = httpx.Timeout(15.0, connect=10.0)
            # Same encoding the workers use, so Content-Length is the size of the raw file
            probe_headers = {"Accept-Encoding": "identity", **headers}
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=await shared_ssl_context_async()) as client:
                # 1. Try HEAD request
                resp = await client.head(url, headers=probe_headers)

                # If HEAD fails or gives no size (signed S3/CDN links often reject HEAD), ask for the
                # first byte only. Streamed and closed unread, so a server that ignores Range
                # never pulls the whole file into memory.
                if resp.status_code >= 400 or "content-length" not in resp.headers:
                    async with client.stream("GET", url, headers={**probe_headers, "Range": "bytes=0-0"}) as resp:
                        pass

                # Check Content-Disposition for filename
                cd = resp.headers.get("content-disposition", "")
                if "filename=" in cd:
                    match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^";\r\n]+)["\']?', cd, re.IGNORECASE)
                    if match:
                        filename = unquote(match.group(1).strip().strip('"\''))

                # Check Accept-Ranges
                accept_ranges = resp.headers.get("accept-ranges", "").lower()
                content_range = resp.headers.get("content-range", "")
                if "bytes" in accept_ranges or "bytes" in content_range:
                    supports_ranges = True

                # Total size: for a 206 the Content-Length is just the requested slice (1 byte),
                # the real size is after the "/" in Content-Range
                range_total = re.search(r"/(\d+)\s*$", content_range)
                if resp.status_code == 206 and range_total:
                    total_bytes = int(range_total.group(1))
                elif resp.status_code < 400 and "content-length" in resp.headers:
                    total_bytes = int(resp.headers["content-length"])

        except Exception as e:
            print(f"[Probe] Error probing URL {url}: {e}")

        # Clean filename from invalid OS characters
        filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
        return {
            "filename": filename,
            "total_bytes": total_bytes,
            "supports_ranges": supports_ranges,
        }

    async def add_download(
        self,
        url: str,
        filename: Optional[str] = None,
        save_dir: Optional[str] = None,
        num_connections: Optional[int] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        referrer: Optional[str] = None,
        auto_start: bool = True,
        format_id: Optional[str] = None,
    ) -> DownloadTask:
        num_connections = num_connections or self.settings.get("default_connections")

        # Check if Magnet or Torrent link
        if url.startswith("magnet:") or url.lower().endswith(".torrent") or (filename and filename.lower().endswith(".torrent")):
            target_save_dir = save_dir or StorageManager.get_category_dir(self.download_dir, "torrent")
            os.makedirs(target_save_dir, exist_ok=True)

            if url.startswith("magnet:"):
                minfo = TorrentManager.parse_magnet_info(url)
                t_filename = sanitize_filename(filename or minfo["name"], "Torrent_Download")
                t_total = minfo.get("total_size", 0)
            else:
                t_filename = sanitize_filename(filename or os.path.basename(url), "download.torrent")
                t_total = 0

            target_path = os.path.join(target_save_dir, t_filename)

            task = DownloadTask(
                url=url,
                filename=t_filename,
                save_path=target_path,
                total_bytes=t_total,
                supports_ranges=True,
                num_connections=16,
                is_torrent=True,
                category="torrent",
            )
            task.chunks = [Chunk(id=0, start_byte=0, end_byte=max(0, t_total - 1), current_byte=0)]

            with self._lock:
                self.tasks[task.id] = task

            self.store.save(task)
            self._notify("task_added", task)

            if auto_start:
                await self.enqueue_or_start(task.id)

            return task

        save_dir = save_dir or self.download_dir
        os.makedirs(save_dir, exist_ok=True)

        info = await self.probe_url(url, custom_headers)
        raw_name = sanitize_filename(filename or info["filename"])

        # Adjust file extension based on selected format
        if format_id == "audio_mp3":
            base, _ = os.path.splitext(raw_name)
            raw_name = f"{base}.mp3"
        elif format_id == "audio_m4a":
            base, _ = os.path.splitext(raw_name)
            raw_name = f"{base}.m4a"
        elif format_id == "subtitle_srt":
            base, _ = os.path.splitext(raw_name)
            raw_name = f"{base}.srt"
        elif format_id == "subtitle_txt":
            base, _ = os.path.splitext(raw_name)
            raw_name = f"{base}.txt"

        is_gdrive = info.get("is_gdrive", False)
        is_extractor = info.get("is_video_platform", False) and not is_gdrive
        is_hls = info.get("is_hls", False)

        # Determine category early for proper folder routing
        if format_id in ("audio_mp3", "audio_m4a"):
            category = "audio"
        elif format_id in ("subtitle_srt", "subtitle_txt"):
            category = "document"
        elif is_hls or is_extractor:
            category = "video"
        else:
            category = DownloadTask.determine_category_for_filename(raw_name)

        # Automatically route to category subfolder if save_dir was not explicitly set or is default download_dir
        if not save_dir or os.path.abspath(save_dir) == os.path.abspath(self.download_dir):
            save_dir = StorageManager.get_category_dir(self.download_dir, category)
        else:
            os.makedirs(save_dir, exist_ok=True)

        final_filename = raw_name
        target_path = os.path.join(save_dir, final_filename)

        # Pick a name no existing file, partial download or unfinished task is using:
        # two tasks sharing one .part file would overwrite each other's data
        in_use = {
            os.path.normcase(os.path.abspath(t.save_path))
            for t in self.tasks.values()
            if t.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        }
        base, ext = os.path.splitext(target_path)
        counter = 1
        while (
            os.path.normcase(os.path.abspath(target_path)) in in_use
            or os.path.exists(target_path)
            or os.path.exists(StorageManager.get_part_path(target_path))
        ):
            target_path = f"{base}_{counter}{ext}"
            counter += 1

        final_filename = os.path.basename(target_path)

        target_url = info.get("resolved_url") or url
        supports_ranges = True if (is_extractor or is_gdrive) else info.get("supports_ranges", False)
        conns = num_connections if supports_ranges else 1
        if (is_extractor or is_gdrive) and conns < 8:
            conns = 16  # Turbo multi-threaded default for video platforms & Google Drive

        task = DownloadTask(
            url=target_url,
            filename=final_filename,
            save_path=target_path,
            total_bytes=info.get("total_bytes", 0),
            supports_ranges=supports_ranges,
            num_connections=conns,
            custom_headers=custom_headers or {},
            referrer=referrer,
            is_hls=is_hls,
            hls_info=info.get("hls_info"),
            is_extractor=is_extractor,
            selected_format=format_id,
        )
        task.category = category

        # Initialize chunks
        if task.supports_ranges and task.total_bytes > 0:
            task.chunks = DynamicSegmenter.create_initial_chunks(task.total_bytes, task.num_connections)
        else:
            task.chunks = [Chunk(id=0, start_byte=0, end_byte=max(0, task.total_bytes - 1), current_byte=0)]

        with self._lock:
            self.tasks[task.id] = task

        self.store.save(task)
        self._notify("task_added", task)

        if auto_start:
            await self.enqueue_or_start(task.id)

        return task

    def active_download_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.status == TaskStatus.DOWNLOADING)

    async def enqueue_or_start(self, task_id: str):
        """
        Starts the task now if fewer than max_active downloads are running, otherwise leaves it
        QUEUED; the scheduler starts queued tasks as slots free up.
        """
        task = self.tasks.get(task_id)
        if not task or task.status == TaskStatus.DOWNLOADING:
            return
        if self.active_download_count() < self.scheduler.max_active_downloads:
            await self.start_task(task_id)
        else:
            task.status = TaskStatus.QUEUED
            self._notify("task_updated", task)

    async def start_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if not task:
            return

        if task.status == TaskStatus.DOWNLOADING:
            return

        task.status = TaskStatus.DOWNLOADING
        task.error_message = None
        self._notify("task_updated", task)

        # Launch the download loop as background task
        loop_task = asyncio.create_task(self._execute_download(task))
        self._running_loops[task_id] = loop_task

    async def _execute_download(self, task: DownloadTask):
        # -1. BitTorrent & Magnet Link Workflow
        if task.is_torrent:
            await self._execute_torrent_download(task)
            return

        # 0. Video Platform Extractor Workflow (YouTube, TikTok, Facebook, etc.)
        if task.is_extractor:
            await self._execute_extractor_download(task)
            return

        # 1. HLS Stream Downloader Workflow
        if task.is_hls:
            await self._execute_hls_download(task)
            return

        part_path = StorageManager.get_part_path(task.save_path)

        # If range supported and size known, preallocate file
        if task.supports_ranges and task.total_bytes > 0:
            StorageManager.preallocate_file(part_path, task.total_bytes)

        file_lock = asyncio.Lock()
        segmenter_lock = asyncio.Lock()
        workers: List[ChunkWorker] = []
        chunk_failures: Dict[int, int] = {}
        give_up_reason: Optional[str] = None
        monitor_task: Optional[asyncio.Task] = None

        def new_worker(worker_id: str) -> ChunkWorker:
            worker = ChunkWorker(
                worker_id=worker_id,
                task=task,
                file_path=part_path,
                file_lock=file_lock,
                rate_limiter=self.rate_limiter,
                on_chunk_progress=lambda: self._notify("task_progress", task),
            )
            workers.append(worker)
            return worker

        def mark_completed():
            for w in workers:
                w.close_file()
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.speed = 0.0
            task.eta_seconds = 0
            StorageManager.finalize_download(task)
            self._notify("task_completed", task)

        def mark_failed(reason: Optional[str]):
            task.status = TaskStatus.ERROR
            task.speed = 0.0
            task.eta_seconds = None
            task.error_message = f"فشل التحميل ({reason or 'خطأ في الاتصال'})"
            self._notify("task_error", task)

        def retries_allowed(worker: ChunkWorker) -> int:
            return PERMANENT_ERROR_RETRIES if worker.last_status in PERMANENT_HTTP_ERRORS else MAX_RETRIES

        # Single stream mode for unknown size or non-range servers (restarts from byte 0 on each attempt)
        async def single_stream_download():
            worker = new_worker("Single")
            if not task.chunks:
                task.chunks = [Chunk(id=0, start_byte=0, end_byte=0, current_byte=0)]
            chunk = task.chunks[0]

            for attempt in range(MAX_RETRIES + 1):
                if attempt:
                    if attempt > retries_allowed(worker):
                        break
                    await asyncio.sleep(min(RETRY_BACKOFF_BASE ** attempt, 5.0))
                if task.status != TaskStatus.DOWNLOADING:
                    return
                if await worker.run_single_stream(chunk):
                    if task.status == TaskStatus.DOWNLOADING:
                        mark_completed()
                    return
            if task.status == TaskStatus.DOWNLOADING:
                mark_failed(worker.last_error)

        # Multi-threaded Range Mode with Dynamic Segmentation
        async def worker_supervisor(worker_id: int):
            nonlocal give_up_reason
            worker = new_worker(f"W{worker_id}")

            while task.status == TaskStatus.DOWNLOADING:
                target_chunk: Optional[Chunk] = None
                async with segmenter_lock:
                    # Find idle chunk
                    for c in task.chunks:
                        if c.status == "idle":
                            target_chunk = c
                            break

                    # Or dynamically split largest running chunk
                    if not target_chunk and task.supports_ranges:
                        target_chunk = DynamicSegmenter.split_largest_chunk_unlocked(task)

                    # Claim it before releasing the lock so no other worker takes the same chunk
                    if target_chunk:
                        target_chunk.status = "downloading"

                if not target_chunk:
                    break

                success = await worker.run_for_chunk(target_chunk)
                if worker.range_ignored:
                    break  # Once all workers stop, the engine falls back to a single connection
                if success or task.status != TaskStatus.DOWNLOADING:
                    continue

                # Retry the same chunk with backoff: network drops get MAX_RETRIES attempts,
                # an expired or forbidden link (4xx) gives up quickly so the user can refresh it
                failures = chunk_failures.get(target_chunk.id, 0) + 1
                chunk_failures[target_chunk.id] = failures
                if failures > retries_allowed(worker):
                    target_chunk.status = "failed"
                    give_up_reason = worker.last_error
                    continue
                await asyncio.sleep(min(RETRY_BACKOFF_BASE ** failures, 5.0))
                if target_chunk.status == "failed":
                    target_chunk.status = "idle"  # Hand it back so this or another worker retries it

        # Speed monitor loop
        async def speed_monitor():
            last_bytes = task.downloaded_bytes
            last_time = time.time()
            last_saved = time.time()

            while task.status == TaskStatus.DOWNLOADING:
                await asyncio.sleep(0.5)
                now = time.time()
                current_bytes = task.downloaded_bytes
                dt = now - last_time
                if dt > 0:
                    speed = (current_bytes - last_bytes) / dt
                    task.speed = max(0.0, speed)
                    last_bytes = current_bytes
                    last_time = now

                    # ETA calculation (unknown when the total size is unknown)
                    if task.speed > 0 and task.total_bytes > 0:
                        task.eta_seconds = int(max(0, task.total_bytes - current_bytes) / task.speed)
                    else:
                        task.eta_seconds = None

                    self._notify("task_progress", task)
                    # Resume points every 2 s are plenty; every status change is saved on its own
                    if now - last_saved >= PROGRESS_SAVE_INTERVAL:
                        self.store.save(task)
                        last_saved = now

        try:
            monitor_task = asyncio.create_task(speed_monitor())

            if not task.supports_ranges or task.total_bytes <= 0:
                await single_stream_download()
                return

            worker_tasks = [asyncio.create_task(worker_supervisor(i)) for i in range(task.num_connections)]
            await asyncio.gather(*worker_tasks)

            # Finalize status
            if task.status != TaskStatus.DOWNLOADING:
                return

            if any(w.range_ignored for w in workers):
                # The server advertised Range support but ignores it: restart on one connection
                print(f"[Engine] Server ignored Range requests for {task.filename}; falling back to a single connection")
                task.supports_ranges = False
                task.num_connections = 1
                task.chunks = [Chunk(id=0, start_byte=0, end_byte=max(0, task.total_bytes - 1), current_byte=0)]
                for w in workers:
                    w.close_file()
                await single_stream_download()
                return

            if all(c.status == "completed" or c.current_byte > c.end_byte for c in task.chunks):
                mark_completed()
            elif any(c.status == "failed" for c in task.chunks):
                mark_failed(give_up_reason)
            else:
                task.status = TaskStatus.PAUSED
                self._notify("task_updated", task)

        except asyncio.CancelledError:
            # Immediate cooperative cancel/pause
            if task.status == TaskStatus.DOWNLOADING:
                task.status = TaskStatus.PAUSED
            task.speed = 0.0
            task.eta_seconds = None
            self._notify("task_updated", task)

        except Exception as e:
            print(f"[Engine] Exception in task {task.id}: {e}")
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            self._notify("task_error", task)

        finally:
            if monitor_task:
                monitor_task.cancel()
            # Clean up all persistent worker HTTP clients
            for w in workers:
                try:
                    await w.close()
                except Exception:
                    pass
            self.store.save(task)

    async def _execute_extractor_download(self, task: DownloadTask, http_headers: Optional[Dict[str, str]] = None):
        """Downloads videos from platforms using yt-dlp with multi-threaded concurrent fragment streams."""
        num_conns = max(8, task.num_connections or 16)
        task.num_connections = num_conns
        task.supports_ranges = True
        task.chunks = [
            Chunk(id=i, start_byte=i, end_byte=100, current_byte=0, status="downloading")
            for i in range(num_conns)
        ]

        def on_progress(downloaded, total, speed, eta):
            if total > 0:
                task.total_bytes = total
                pct = (downloaded / total) * 100
                for i, chunk in enumerate(task.chunks):
                    phase_offset = (i % 3) * 2
                    chunk_pct = min(100, max(0, int(pct + (phase_offset if pct < 95 else 0))))
                    chunk.current_byte = chunk_pct
                    chunk.status = "completed" if chunk_pct >= 100 else "downloading"
            task.speed = speed or 0.0
            task.eta_seconds = eta
            self._notify("task_progress", task)

        try:
            fmt = task.selected_format or "bestvideo+bestaudio/best"
            if VideoExtractor.needs_ffmpeg(fmt) and not HLSDownloader.find_ffmpeg():
                await self._prepare_ffmpeg(task)
            final_path = await VideoExtractor.download(
                url=task.url,
                output_path=task.save_path,
                format_selector=fmt,
                num_connections=num_conns,
                on_progress=on_progress,
                is_cancelled=lambda: task.status != TaskStatus.DOWNLOADING,
                http_headers=http_headers,
            )

            if task.status == TaskStatus.DOWNLOADING:
                if final_path:
                    if final_path != task.save_path:
                        # e.g. .webm when ffmpeg is missing, or "<title>.<lang>.srt": point at the real file
                        task.save_path = final_path
                        task.filename = os.path.basename(final_path)
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    task.speed = 0.0
                    task.eta_seconds = 0
                    for chunk in task.chunks:
                        chunk.status = "completed"
                        chunk.current_byte = 100
                    if os.path.exists(task.save_path):
                        task.total_bytes = os.path.getsize(task.save_path)
                    self._notify("task_completed", task)
                else:
                    task.status = TaskStatus.ERROR
                    task.error_message = "فشل تحميل الفيديو من المنصة"
                    self._notify("task_error", task)
        except asyncio.CancelledError:
            # Keep CANCELLED (set by cancel_task); only an interrupted download becomes PAUSED
            if task.status == TaskStatus.DOWNLOADING:
                task.status = TaskStatus.PAUSED
            self._notify("task_updated", task)
        except Exception as e:
            print(f"[Engine] VideoExtractor error: {e}")
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            self._notify("task_error", task)
        finally:
            task.phase = None
            self.store.save(task)

    async def _prepare_ffmpeg(self, task: DownloadTask):
        """
        Downloads OpenHydro's ffmpeg the first time a video needs it (merging video with audio, MP3).
        If that is not possible the download still goes ahead, in the best quality that needs none.
        """
        def on_progress(done: int, total: int):
            task.phase = f"تجهيز ffmpeg لأول مرة (دمج الصوت والصورة): {done * 100 // max(total, 1)}% من {total // 2**20} MB"
            self._notify("task_progress", task)

        try:
            if not ffmpeg_component.supported():
                raise RuntimeError("ffmpeg is not installed")
            task.phase = "تجهيز ffmpeg لأول مرة (دمج الصوت والصورة)..."
            self._notify("task_updated", task)
            await asyncio.to_thread(ffmpeg_component.ensure, on_progress)
            task.phase = None
        except Exception as e:
            print(f"[Engine] ffmpeg unavailable: {e}")
            task.phase = "ffmpeg غير متاح: التنزيل بأفضل جودة لا تحتاج دمج"
        self._notify("task_updated", task)

    async def _execute_hls_download(self, task: DownloadTask):
        """Executes HLS (.m3u8) stream downloading and remuxing."""
        temp_dir = task.save_path + "_hls_temp"
        os.makedirs(temp_dir, exist_ok=True)

        # Playlist, segments (and keys, via yt-dlp) all need the same headers as the page
        hls_headers = {"User-Agent": DEFAULT_USER_AGENT, **task.custom_headers}
        if task.referrer:
            hls_headers["Referer"] = task.referrer

        try:
            # 1. Resolve media playlist
            playlist_url = task.url
            if task.hls_info and task.hls_info.get("type") == "master":
                qualities = task.hls_info.get("qualities", [])
                if qualities:
                    # Choose highest quality
                    playlist_url = qualities[0]["url"]

            media_info = await HLSStreamParser.fetch_and_parse(playlist_url, hls_headers)
            if media_info.get("type") == "master":
                # Nested master, resolve first quality
                playlist_url = media_info["qualities"][0]["url"]
                media_info = await HLSStreamParser.fetch_and_parse(playlist_url, hls_headers)

            if media_info.get("encrypted") or media_info.get("fmp4"):
                # Plain concatenation would produce an unplayable file: yt-dlp's HLS downloader
                # handles AES-128 decryption and fMP4 init segments
                shutil.rmtree(temp_dir, ignore_errors=True)
                await self._execute_extractor_download(task, http_headers=hls_headers)
                return

            segments = media_info.get("segments", [])
            if not segments:
                raise ValueError("No TS segments found in media playlist")

            total_segs = len(segments)
            # Create chunks for visualization
            num_display_chunks = min(8, total_segs)
            task.chunks = [
                Chunk(
                    id=i,
                    start_byte=0,
                    end_byte=100,
                    current_byte=0,
                    status="idle"
                ) for i in range(num_display_chunks)
            ]

            last_time = time.time()
            last_bytes = 0

            def on_hls_progress(completed_segs, total_s, current_bytes):
                nonlocal last_time, last_bytes
                now = time.time()
                dt = now - last_time
                if dt >= 0.5:
                    speed = (current_bytes - last_bytes) / dt
                    task.speed = max(0.0, speed)
                    last_bytes = current_bytes
                    last_time = now

                    # Update visual chunks
                    fraction = completed_segs / total_s
                    task.total_bytes = int(current_bytes / fraction) if fraction > 0 else 0
                    for ci, chunk in enumerate(task.chunks):
                        c_frac = min(1.0, max(0.0, (fraction * num_display_chunks) - ci))
                        chunk.current_byte = int(c_frac * 100)
                        chunk.status = "completed" if c_frac >= 1.0 else ("downloading" if c_frac > 0 else "idle")

                    self._notify("task_progress", task)

            # 2. Download segments
            downloaded = await HLSDownloader.download_segments(
                segments=segments,
                temp_dir=temp_dir,
                num_connections=task.num_connections or 8,
                rate_limiter=self.rate_limiter,
                on_progress=on_hls_progress,
                is_cancelled=lambda: task.status != TaskStatus.DOWNLOADING,
                headers=hls_headers,
            )

            if task.status != TaskStatus.DOWNLOADING:
                return

            # A gap in the segment list means a broken video: report it (resume retries only the missing ones)
            missing = sum(1 for path in downloaded if not path)
            if missing:
                raise RuntimeError(f"تعذر تحميل {missing} من {total_segs} جزء من البث")

            # 3. Assemble stream into final video file (FFmpeg runs off the event loop); without
            #    ffmpeg the segments are joined into a .ts instead of the requested .mp4
            if task.save_path.lower().endswith(".mp4") and not HLSDownloader.find_ffmpeg():
                await self._prepare_ffmpeg(task)
            output_path = await asyncio.to_thread(HLSDownloader.assemble_stream, downloaded, task.save_path)
            if output_path:
                if output_path != task.save_path:
                    # Without FFmpeg the stream is saved as .ts rather than mislabelled .mp4
                    task.save_path = output_path
                    task.filename = os.path.basename(output_path)
                task.total_bytes = os.path.getsize(output_path)
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                task.speed = 0.0
                task.eta_seconds = 0
                for c in task.chunks:
                    c.status = "completed"
                    c.current_byte = 100

                # Clean up temporary segments directory
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

                self._notify("task_completed", task)
            else:
                raise RuntimeError("Failed to assemble downloaded HLS segments")

        except asyncio.CancelledError:
            if task.status == TaskStatus.DOWNLOADING:
                task.status = TaskStatus.PAUSED
            task.speed = 0.0
            self._notify("task_updated", task)
        except Exception as e:
            print(f"[Engine] HLS Exception: {e}")
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            self._notify("task_error", task)
        finally:
            task.phase = None
            self.store.save(task)

    async def _execute_torrent_download(self, task: DownloadTask):
        """Downloads BitTorrent or Magnet links via embedded aria2 JSON-RPC engine."""
        if not self.torrent_manager.is_available():
            task.status = TaskStatus.ERROR
            task.error_message = "محرك التورنت (aria2c) غير مثبت أو غير متاح"
            self._notify("task_error", task)
            return

        target_save_dir = os.path.dirname(task.save_path)
        os.makedirs(target_save_dir, exist_ok=True)

        try:
            # If GID doesn't exist yet, add URI or .torrent file
            if not task.torrent_gid:
                if task.url.startswith("magnet:"):
                    gid = await self.torrent_manager.add_magnet(task.url, save_dir=target_save_dir)
                elif task.url.lower().endswith(".torrent"):
                    if os.path.exists(task.url):
                        gid = await self.torrent_manager.add_torrent_file(task.url, save_dir=target_save_dir)
                    else:
                        async with httpx.AsyncClient(timeout=15.0, verify=await shared_ssl_context_async()) as client:
                            r = await client.get(task.url, follow_redirects=True)
                            r.raise_for_status()
                            gid = await self.torrent_manager.add_torrent_file(r.content, save_dir=target_save_dir)
                else:
                    gid = await self.torrent_manager.add_magnet(task.url, save_dir=target_save_dir)
                task.torrent_gid = gid
                self.store.save(task)
            else:
                # Resumed manually or started from the queue: un-pause it in aria2
                await self.torrent_manager.resume(task.torrent_gid)

            # Polling loop
            unreachable_since: Optional[float] = None
            while task.status == TaskStatus.DOWNLOADING:
                await asyncio.sleep(1.0)
                try:
                    status = await self.torrent_manager.tell_status(task.torrent_gid)
                    unreachable_since = None
                except Exception:
                    # aria2 busy or restarting: keep waiting, and only give up after half a minute
                    unreachable_since = unreachable_since or time.time()
                    if time.time() - unreachable_since > 30:
                        raise
                    continue
                if not status:
                    # GID was lost from aria2c (e.g. app restarted) -> re-register download
                    try:
                        if task.url.startswith("magnet:"):
                            task.torrent_gid = await self.torrent_manager.add_magnet(task.url, save_dir=target_save_dir)
                        elif task.url.lower().endswith(".torrent") and os.path.exists(task.url):
                            task.torrent_gid = await self.torrent_manager.add_torrent_file(task.url, save_dir=target_save_dir)
                        self.store.save(task)
                    except Exception:
                        pass
                    continue

                # Check for followedBy (Magnet metadata -> actual files GID)
                followed_by = status.get("followedBy")
                if followed_by and len(followed_by) > 0:
                    task.torrent_gid = followed_by[0]
                    self.store.save(task)
                    continue

                state = status.get("status")  # active, waiting, paused, error, complete, removed
                completed_len = int(status.get("completedLength", 0))
                total_len = int(status.get("totalLength", 0))
                speed = float(status.get("downloadSpeed", 0.0))
                seeders = int(status.get("numSeeders", 0))
                connections = int(status.get("connections", 0))

                task.total_bytes = max(task.total_bytes, total_len)
                task.speed = speed
                task.seeders = seeders
                task.peers = connections

                if task.chunks:
                    task.chunks[0].current_byte = completed_len
                    task.chunks[0].end_byte = max(0, total_len - 1)
                    task.chunks[0].status = "downloading"

                # Check if torrent meta resolved real filename
                bt_info = status.get("bittorrent", {}).get("info", {})
                bt_name = sanitize_filename(bt_info.get("name"), task.filename)
                if bt_name != task.filename:
                    task.filename = bt_name
                    task.save_path = os.path.join(target_save_dir, task.filename)

                if speed > 0 and total_len > completed_len:
                    task.eta_seconds = int((total_len - completed_len) / speed)
                else:
                    task.eta_seconds = None

                if state == "complete":
                    if status.get("followedBy"):
                        task.torrent_gid = status["followedBy"][0]
                        self.store.save(task)
                        continue

                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    task.speed = 0.0
                    task.eta_seconds = 0
                    if task.chunks:
                        task.chunks[0].status = "completed"
                        task.chunks[0].current_byte = total_len
                    self.store.save(task)
                    self._notify("task_completed", task)
                    return

                if state == "error":
                    err_msg = status.get("errorMessage", "حدث خطأ أثناء تحميل التورنت")
                    task.status = TaskStatus.ERROR
                    task.error_message = err_msg
                    self._notify("task_error", task)
                    return

                self._notify("task_progress", task)
                self.store.save(task)

        except asyncio.CancelledError:
            if task.status == TaskStatus.DOWNLOADING:
                task.status = TaskStatus.PAUSED
            task.speed = 0.0
            # A cancelled torrent was already removed from aria2 by cancel_task
            if task.torrent_gid and task.status == TaskStatus.PAUSED:
                await self.torrent_manager.pause(task.torrent_gid)
            self._notify("task_updated", task)
        except Exception as e:
            print(f"[Engine] Torrent error: {e}")
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            self._notify("task_error", task)
        finally:
            self.store.save(task)

    async def pause_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task and task.status == TaskStatus.QUEUED:
            # Hold a waiting task so the scheduler does not start it
            task.status = TaskStatus.PAUSED
            self.store.save(task)
            self._notify("task_updated", task)
        elif task and task.status == TaskStatus.DOWNLOADING:
            task.status = TaskStatus.PAUSED
            task.speed = 0.0
            task.eta_seconds = None

            if task.is_torrent and task.torrent_gid:
                try:
                    await self.torrent_manager.pause(task.torrent_gid)
                except Exception:
                    pass

            # Cancel running asyncio loop task for immediate stop
            loop_task = self._running_loops.get(task_id)
            if loop_task and not loop_task.done():
                loop_task.cancel()

            self.store.save(task)
            self._notify("task_updated", task)

    async def resume_task(self, task_id: str, respect_queue: bool = False):
        """
        Resumes a paused/failed task. A manual resume starts at once; bulk resumes
        (tray "resume all", scheduled start) pass respect_queue to honour max_active.
        """
        task = self.tasks.get(task_id)
        if task and task.status in (TaskStatus.PAUSED, TaskStatus.ERROR):
            # Reset chunk statuses from error/downloading to idle
            for c in task.chunks:
                if c.status in ("failed", "downloading") or c.current_byte <= c.end_byte:
                    if c.status != "completed":
                        c.status = "idle"
            if respect_queue:
                await self.enqueue_or_start(task_id)
            else:
                await self.start_task(task_id)

    async def pause_all(self):
        for task in list(self.tasks.values()):
            if task.status in (TaskStatus.DOWNLOADING, TaskStatus.QUEUED):
                await self.pause_task(task.id)

    async def resume_all(self):
        for task in sorted(self.tasks.values(), key=lambda t: t.created_at):
            if task.status in (TaskStatus.PAUSED, TaskStatus.ERROR):
                await self.resume_task(task.id, respect_queue=True)

    async def refresh_task_url(self, task_id: str, new_url: str):
        """
        Updates an expired download address with a fresh link and resumes download.
        (IDM Smart Address Refresh).
        """
        task = self.tasks.get(task_id)
        if not task:
            return False

        from .gdrive import GoogleDriveHandler
        if GoogleDriveHandler.is_gdrive_file(new_url):
            try:
                ginfo = await GoogleDriveHandler.resolve_direct_download(new_url)
                new_url = ginfo.get("resolved_url") or new_url
            except Exception:
                pass

        task.url = new_url
        self.store.save(task)
        if task.status in (TaskStatus.PAUSED, TaskStatus.ERROR):
            await self.resume_task(task_id)
        return True

    async def cancel_task(self, task_id: str, delete_file: bool = False):
        task = self.tasks.get(task_id)
        if task:
            task.status = TaskStatus.CANCELLED
            task.speed = 0.0
            if task.is_torrent and task.torrent_gid:
                try:
                    await self.torrent_manager.remove(task.torrent_gid)
                except Exception:
                    pass
            loop_task = self._running_loops.get(task_id)
            if loop_task and not loop_task.done():
                loop_task.cancel()
                # Let workers release their .part handles first (Windows cannot delete open files)
                await asyncio.wait({loop_task}, timeout=5.0)

            if delete_file:
                paths = [StorageManager.get_part_path(task.save_path), task.save_path]
                if task.is_torrent:
                    paths.append(task.save_path + ".aria2")  # aria2 control file
                for p in paths:
                    # Multi-file torrents download into a folder
                    self._remove_path(p, allow_dir=task.is_torrent)
            self._notify("task_cancelled", task)

    async def remove_task(self, task_id: str, delete_file: bool = False):
        """
        Stops a task and forgets it. Its stored record and temporary .part are always removed so it
        does not come back after a restart; the downloaded file itself is deleted only on request.
        """
        task = self.tasks.get(task_id)
        if not task:
            return
        await self.cancel_task(task_id, delete_file=delete_file)
        self._remove_path(StorageManager.get_part_path(task.save_path))
        self.store.delete(task_id)
        with self._lock:
            self.tasks.pop(task_id, None)
        self._running_loops.pop(task_id, None)

    @staticmethod
    def _remove_path(path: str, allow_dir: bool = False):
        try:
            if allow_dir and os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            print(f"[Engine] Could not remove {path}: {e}")
