"""
OpenHydro Chunk Worker
Handles HTTP Range requests, direct disk writes, and dynamic chunk handoff.
Uses os.pwrite-equivalent (seek+write under lock) for thread-safe random-access I/O.
"""

import time
import asyncio
import httpx
from typing import Optional, Callable
from .task import Chunk, DownloadTask, TaskStatus
from .limiter import RateLimiter


CHUNK_BUFFER_SIZE = 64 * 1024  # 64 KB read buffer
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 OpenHydro/1.0"

# Max consecutive failures before giving up on a chunk
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 1.5  # exponential backoff seconds


class ChunkWorker:
    def __init__(
        self,
        worker_id: str,
        task: DownloadTask,
        file_path: str,
        file_lock: asyncio.Lock,
        rate_limiter: RateLimiter,
        on_chunk_progress: Optional[Callable] = None,
    ):
        self.worker_id = worker_id
        self.task = task
        self.file_path = file_path
        self.file_lock = file_lock
        self.rate_limiter = rate_limiter
        self.on_chunk_progress = on_chunk_progress
        # Persistent HTTP client for connection reuse (Keep-Alive / TLS session reuse)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily creates and reuses a single httpx.AsyncClient per worker for connection pooling."""
        if self._client is None or self._client.is_closed:
            timeout = httpx.Timeout(30.0, connect=10.0)
            self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        return self._client

    async def close(self):
        """Closes the persistent HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def run_for_chunk(self, chunk: Chunk) -> bool:
        """
        Downloads a specific chunk using HTTP Range.
        Returns True if chunk finished completely.
        Writes directly to file at correct offset using per-worker file handle.
        """
        chunk.status = "downloading"
        chunk.active_worker_id = self.worker_id

        # If already done
        if chunk.current_byte > chunk.end_byte:
            chunk.status = "completed"
            return True

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Encoding": "identity",  # Raw bytes without gzip/deflate re-encoding
            "Range": f"bytes={chunk.current_byte}-{chunk.end_byte}",
            **self.task.custom_headers,
        }
        if self.task.referrer:
            headers["Referer"] = self.task.referrer

        last_speed_time = time.time()
        bytes_since_last_calc = 0

        try:
            client = await self._get_client()

            async with client.stream("GET", self.task.url, headers=headers) as response:
                # 206 Partial Content or 200 OK (if single stream)
                if response.status_code not in (206, 200):
                    chunk.status = "failed"
                    return False

                async for data in response.aiter_bytes(chunk_size=CHUNK_BUFFER_SIZE):
                    if self.task.status in (TaskStatus.PAUSED, TaskStatus.CANCELLED):
                        chunk.status = "idle"
                        return False

                    # Check dynamic boundary BEFORE writing — trim data if it would
                    # exceed the chunk's (possibly dynamically reduced) end_byte.
                    remaining = chunk.end_byte - chunk.current_byte + 1
                    if remaining <= 0:
                        break
                    if len(data) > remaining:
                        data = data[:remaining]

                    # Rate limiting
                    await self.rate_limiter.acquire(len(data))

                    # Write at exact position using a per-worker independent file handle.
                    # This avoids the seek+write race condition of a shared handle.
                    offset = chunk.current_byte
                    async with self.file_lock:
                        with open(self.file_path, "r+b") as fh:
                            fh.seek(offset)
                            fh.write(data)

                    chunk.current_byte += len(data)
                    bytes_since_last_calc += len(data)

                    # Update chunk instantaneous speed
                    now = time.time()
                    delta = now - last_speed_time
                    if delta >= 0.5:
                        chunk.speed = bytes_since_last_calc / delta
                        last_speed_time = now
                        bytes_since_last_calc = 0
                        if self.on_chunk_progress:
                            self.on_chunk_progress()

                    # Check if chunk reached its boundary (which might have been split dynamically!)
                    if chunk.current_byte > chunk.end_byte:
                        break

            chunk.status = "completed"
            chunk.speed = 0.0
            return True

        except Exception as e:
            print(f"[Worker-{self.worker_id}] Error in chunk {chunk.id}: {e}")
            chunk.status = "failed"
            chunk.speed = 0.0
            return False

    async def run_single_stream(self, chunk: Chunk) -> bool:
        """
        Downloads file as a single stream without Range header.
        Used when file size is unknown or server doesn't support ranges.
        """
        chunk.status = "downloading"
        chunk.active_worker_id = self.worker_id

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            **self.task.custom_headers,
        }
        if self.task.referrer:
            headers["Referer"] = self.task.referrer

        last_speed_time = time.time()
        bytes_since_last_calc = 0

        try:
            client = await self._get_client()

            async with client.stream("GET", self.task.url, headers=headers) as response:
                if response.status_code not in (200, 206):
                    chunk.status = "failed"
                    return False

                # Update total_bytes from Content-Length if available now
                cl = response.headers.get("content-length")
                if cl and self.task.total_bytes == 0:
                    self.task.total_bytes = int(cl)

                with open(self.file_path, "wb") as fh:
                    async for data in response.aiter_bytes(chunk_size=CHUNK_BUFFER_SIZE):
                        if self.task.status in (TaskStatus.PAUSED, TaskStatus.CANCELLED):
                            chunk.status = "idle"
                            return False

                        await self.rate_limiter.acquire(len(data))

                        fh.write(data)

                        chunk.current_byte += len(data)
                        chunk.end_byte = max(chunk.end_byte, chunk.current_byte - 1)
                        bytes_since_last_calc += len(data)

                        now = time.time()
                        delta = now - last_speed_time
                        if delta >= 0.5:
                            chunk.speed = bytes_since_last_calc / delta
                            last_speed_time = now
                            bytes_since_last_calc = 0
                            if self.on_chunk_progress:
                                self.on_chunk_progress()

            self.task.total_bytes = chunk.current_byte
            chunk.end_byte = chunk.current_byte - 1
            chunk.status = "completed"
            chunk.speed = 0.0
            return True

        except Exception as e:
            print(f"[Worker-{self.worker_id}] Stream error: {e}")
            chunk.status = "failed"
            chunk.speed = 0.0
            return False
