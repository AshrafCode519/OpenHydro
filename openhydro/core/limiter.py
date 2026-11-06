"""
OpenHydro Dynamic Bandwidth Rate Limiter
Uses Token Bucket algorithm for precise, non-blocking network throttling.
"""

import time
import asyncio
import threading


class RateLimiter:
    def __init__(self, max_bytes_per_second: int = 0):
        """
        max_bytes_per_second: 0 means unlimited.
        """
        self.max_bytes_per_second = max_bytes_per_second
        self.tokens = float(max_bytes_per_second)
        self.last_update = time.time()
        self._lock = threading.Lock()

    def set_limit(self, max_bytes_per_second: int):
        with self._lock:
            self.max_bytes_per_second = max_bytes_per_second
            self.tokens = float(max_bytes_per_second)
            self.last_update = time.time()

    def _take(self, amount: int) -> float:
        """Takes the tokens for `amount` bytes if available (0.0), else the seconds to wait first."""
        with self._lock:
            if self.max_bytes_per_second <= 0:
                return 0.0  # Unlimited (also when the limit was lifted while waiting)
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now

            # Add newly generated tokens
            self.tokens = min(
                float(self.max_bytes_per_second),
                self.tokens + elapsed * self.max_bytes_per_second
            )

            # A read bigger than one second's worth never fits the bucket (it used to wait forever): it
            # waits for a full bucket and leaves a debt the next reads wait for, so the average holds
            needed = min(amount, self.max_bytes_per_second)
            if self.tokens >= needed:
                self.tokens -= amount
                return 0.0
            return min((needed - self.tokens) / self.max_bytes_per_second, 0.1)

    async def acquire(self, amount: int):
        """
        Asynchronously waits until enough tokens are available to send/receive `amount` bytes.
        """
        while True:
            wait = self._take(amount)
            if not wait:
                return
            await asyncio.sleep(wait)

    def acquire_blocking(self, amount: int):
        """The same for code running in its own thread (yt-dlp's progress hook)."""
        while True:
            wait = self._take(amount)
            if not wait:
                return
            time.sleep(wait)


class LimiterChain:
    """Waits for every limiter in turn: the global speed limit and a download's own."""

    def __init__(self, *limiters: RateLimiter):
        self.limiters = limiters

    async def acquire(self, amount: int):
        for limiter in self.limiters:
            await limiter.acquire(amount)

    def acquire_blocking(self, amount: int):
        for limiter in self.limiters:
            limiter.acquire_blocking(amount)

