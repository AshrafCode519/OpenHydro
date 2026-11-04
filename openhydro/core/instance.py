"""
OpenHydro Single-Instance Guard
A second launch (e.g. the desktop icon while the autostarted --headless copy runs) must not start
another engine, tray icon and server: it asks the running instance to show its window and exits.
"""

import os
import sys
import time
import urllib.request
from typing import Optional

from .security import get_app_data_dir

ERROR_ALREADY_EXISTS = 183


class InstanceLock:
    """Held for the whole process lifetime; `acquired` is False when another instance owns it."""

    def __init__(self, name: str = "OpenHydro.SingleInstance"):
        self._handle: Optional[int] = None
        self._file = None
        self.acquired = self._acquire(name)

    def _acquire(self, name: str) -> bool:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            # Per-user ("Local\") named mutex: instant, and released by Windows if the process dies
            self._handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
            return ctypes.get_last_error() != ERROR_ALREADY_EXISTS

        import fcntl
        self._file = open(os.path.join(get_app_data_dir(), f"{name}.lock"), "w")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def release(self):
        if self._handle:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None
        if self._file:
            self._file.close()
            self._file = None


def ask_running_instance_to_show(base_url: str, attempts: int = 10) -> bool:
    """Asks the instance that owns the lock to bring up its window (it may still be starting)."""
    # Straight to localhost: a system proxy must not intercept this request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f"{base_url}/api/app/show", method="POST")
    for attempt in range(attempts):
        try:
            with opener.open(request, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            if attempt < attempts - 1:
                time.sleep(1)
    return False
