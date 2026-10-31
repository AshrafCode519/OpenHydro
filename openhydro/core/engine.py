"""
OpenHydro Core Download Manager Engine
Orchestrates downloads, queues, dynamic segmentation, state restoration, and task lifecycles.
"""

import os
import re
import time
import asyncio
import threading
from urllib.parse import urlparse, unquote
from typing import Dict, List, Optional, Callable
import httpx

from .task import DownloadTask, Chunk, TaskStatus
from .segmenter import DynamicSegmenter
from .storage import StorageManager
from .limiter import RateLimiter
from .worker import ChunkWorker, DEFAULT_USER_AGENT, MAX_RETRIES, RETRY_BACKOFF_BASE
from .hls import HLSStreamParser, HLSDownloader
from .scheduler import SmartScheduler
from .extractor import VideoExtractor


class DownloadEngine:
    def __init__(self, download_dir: Optional[str] = None):
        self.download_dir = download_dir or StorageManager.get_default_download_dir()
        self.tasks: Dict[str, DownloadTask] = {}
        self.rate_limiter = RateLimiter(0)  # Unlimited by default
        self._running_loops: Dict[str, asyncio.Task] = {}
        self._listeners: List[Callable] = []
        self._lock = threading.Lock()
        self.scheduler = SmartScheduler(self)

        # Restore saved tasks from disk
        self._restore_persisted_tasks()

    def _restore_persisted_tasks(self):
        """Scans download directory on startup and restores interrupted/completed tasks."""
        try:
            persisted = StorageManager.scan_and_load_tasks(self.download_dir)
            with self._lock:
                for task in persisted:
                    self.tasks[task.id] = task
        except Exception as e:
            print(f"[Engine] Error restoring tasks: {e}")

    def register_listener(self, callback: Callable):
        self._listeners.append(callback)

    def _notify(self, event_type: str, task: DownloadTask):
        for listener in self._listeners:
            try:
                listener(event_type, task.to_dict())
            except Exception as e:
                print(f"[Engine] Listener error: {e}")

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
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                # 1. Try HEAD request
                resp = await client.head(url, headers=headers)

                # If HEAD fails or gives no size, try GET with Range 0-0
                if resp.status_code >= 400 or "content-length" not in resp.headers:
                    headers["Range"] = "bytes=0-0"
                    resp = await client.get(url, headers=headers)

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

                # Check Content-Length
                if "content-length" in resp.headers:
                    total_bytes = int(resp.headers["content-length"])
                elif "content-range" in resp.headers:
                    match = re.search(r"/(\d+)", content_range)
                    if match:
                        total_bytes = int(match.group(1))

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
        num_connections: int = 8,
        custom_headers: Optional[Dict[str, str]] = None,
        referrer: Optional[str] = None,
        auto_start: bool = True,
        format_id: Optional[str] = None,
    ) -> DownloadTask:
        save_dir = save_dir or self.download_dir
        os.makedirs(save_dir, exist_ok=True)

        info = await self.probe_url(url, custom_headers)
        raw_name = filename or info["filename"]

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

        # Handle duplicate filenames
        base, ext = os.path.splitext(target_path)
        counter = 1
        while os.path.exists(target_path) and not os.path.exists(StorageManager.get_part_path(target_path)):
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

        StorageManager.save_task_meta(task)
        self._notify("task_added", task)

        if auto_start:
            asyncio.create_task(self.start_task(task.id))

        return task

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

        try:
            # 1. Single stream mode for unknown size or non-range servers
            if not task.supports_ranges or task.total_bytes <= 0:
                worker = ChunkWorker(
                    worker_id="Single",
                    task=task,
                    file_path=part_path,
                    file_lock=file_lock,
                    rate_limiter=self.rate_limiter,
                    on_chunk_progress=lambda: self._notify("task_progress", task),
                )
                workers.append(worker)

                chunk = task.chunks[0] if task.chunks else Chunk(id=0, start_byte=0, end_byte=0, current_byte=0)
                if not task.chunks:
                    task.chunks = [chunk]

                success = await worker.run_single_stream(chunk)
                if success and task.status == TaskStatus.DOWNLOADING:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    task.speed = 0.0
                    task.eta_seconds = 0
                    StorageManager.finalize_download(task)
                    self._notify("task_completed", task)
                return

            # 2. Multi-threaded Range Mode with Dynamic Segmentation
            async def worker_supervisor(worker_id: int):
                worker = ChunkWorker(
                    worker_id=f"W{worker_id}",
                    task=task,
                    file_path=part_path,
                    file_lock=file_lock,
                    rate_limiter=self.rate_limiter,
                    on_chunk_progress=lambda: self._notify("task_progress", task),
                )
                workers.append(worker)

                retries = 0
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

                    if not target_chunk:
                        break

                    success = await worker.run_for_chunk(target_chunk)
                    if not success and task.status == TaskStatus.DOWNLOADING:
                        retries += 1
                        if retries > MAX_RETRIES:
                            target_chunk.status = "failed"
                            break
                        backoff = RETRY_BACKOFF_BASE ** retries
                        await asyncio.sleep(min(backoff, 5.0))
                    else:
                        retries = 0

            # Speed monitor loop
            async def speed_monitor():
                last_bytes = task.downloaded_bytes
                last_time = time.time()

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

                        # ETA calculation
                        rem = max(0, task.total_bytes - current_bytes)
                        if task.speed > 0:
                            task.eta_seconds = int(rem / task.speed)
                        else:
                            task.eta_seconds = None

                        self._notify("task_progress", task)
                        StorageManager.save_task_meta(task)

            num_workers = task.num_connections if task.supports_ranges else 1
            worker_tasks = [asyncio.create_task(worker_supervisor(i)) for i in range(num_workers)]
            monitor_task = asyncio.create_task(speed_monitor())

            await asyncio.gather(*worker_tasks)
            monitor_task.cancel()

            # Finalize status
            if task.status == TaskStatus.DOWNLOADING:
                all_done = all(c.status == "completed" or c.current_byte > c.end_byte for c in task.chunks)
                if all_done:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    task.speed = 0.0
                    task.eta_seconds = 0
                    StorageManager.finalize_download(task)
                    self._notify("task_completed", task)
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
            # Clean up all persistent worker HTTP clients
            for w in workers:
                try:
                    await w.close()
                except Exception:
                    pass
            StorageManager.save_task_meta(task)

    async def _execute_extractor_download(self, task: DownloadTask):
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
            success = await VideoExtractor.download(
                url=task.url,
                output_path=task.save_path,
                format_selector=fmt,
                num_connections=num_conns,
                on_progress=on_progress,
                is_cancelled=lambda: task.status != TaskStatus.DOWNLOADING,
            )

            if task.status == TaskStatus.DOWNLOADING:
                if success:
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
            task.status = TaskStatus.PAUSED
            self._notify("task_updated", task)
        except Exception as e:
            print(f"[Engine] VideoExtractor error: {e}")
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            self._notify("task_error", task)
        finally:
            StorageManager.save_task_meta(task)

    async def _execute_hls_download(self, task: DownloadTask):
        """Executes HLS (.m3u8) stream downloading and remuxing."""
        import shutil

        temp_dir = task.save_path + "_hls_temp"
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # 1. Resolve media playlist
            playlist_url = task.url
            if task.hls_info and task.hls_info.get("type") == "master":
                qualities = task.hls_info.get("qualities", [])
                if qualities:
                    # Choose highest quality
                    playlist_url = qualities[0]["url"]

            media_info = await HLSStreamParser.fetch_and_parse(playlist_url, task.custom_headers)
            if media_info.get("type") == "master":
                # Nested master, resolve first quality
                playlist_url = media_info["qualities"][0]["url"]
                media_info = await HLSStreamParser.fetch_and_parse(playlist_url, task.custom_headers)

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
            )

            if task.status != TaskStatus.DOWNLOADING:
                return

            # 3. Assemble stream into final video file
            success = HLSDownloader.assemble_stream(downloaded, task.save_path)
            if success:
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
            task.status = TaskStatus.PAUSED
            task.speed = 0.0
            self._notify("task_updated", task)
        except Exception as e:
            print(f"[Engine] HLS Exception: {e}")
            task.status = TaskStatus.ERROR
            task.error_message = str(e)
            self._notify("task_error", task)
        finally:
            StorageManager.save_task_meta(task)

    async def pause_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task and task.status == TaskStatus.DOWNLOADING:
            task.status = TaskStatus.PAUSED
            task.speed = 0.0
            task.eta_seconds = None

            # Cancel running asyncio loop task for immediate stop
            loop_task = self._running_loops.get(task_id)
            if loop_task and not loop_task.done():
                loop_task.cancel()

            StorageManager.save_task_meta(task)
            self._notify("task_updated", task)

    async def resume_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task and task.status in (TaskStatus.PAUSED, TaskStatus.ERROR):
            # Reset chunk statuses from error/downloading to idle
            for c in task.chunks:
                if c.status in ("failed", "downloading") or c.current_byte <= c.end_byte:
                    if c.status != "completed":
                        c.status = "idle"
            await self.start_task(task_id)

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
        StorageManager.save_task_meta(task)
        if task.status in (TaskStatus.PAUSED, TaskStatus.ERROR):
            await self.resume_task(task_id)
        return True

    async def cancel_task(self, task_id: str, delete_file: bool = False):
        task = self.tasks.get(task_id)
        if task:
            task.status = TaskStatus.CANCELLED
            task.speed = 0.0
            loop_task = self._running_loops.get(task_id)
            if loop_task and not loop_task.done():
                loop_task.cancel()

            if delete_file:
                part_path = StorageManager.get_part_path(task.save_path)
                meta_path = StorageManager.get_meta_path(task.save_path)
                for p in [part_path, meta_path, task.save_path]:
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
            self._notify("task_cancelled", task)
