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

    async def acquire(self, amount: int):
        """
        Asynchronously waits until enough tokens are available to send/receive `amount` bytes.
        """
        if self.max_bytes_per_second <= 0:
            return  # Unlimited

        while True:
            with self._lock:
                now = time.time()
                elapsed = now - self.last_update
                self.last_update = now

                # Add newly generated tokens
                self.tokens = min(
                    float(self.max_bytes_per_second),
                    self.tokens + elapsed * self.max_bytes_per_second
                )

                if self.tokens >= amount:
                    self.tokens -= amount
                    return

                # Calculate needed sleep time
                needed = amount - self.tokens
                sleep_time = needed / self.max_bytes_per_second

            await asyncio.sleep(min(sleep_time, 0.1))

