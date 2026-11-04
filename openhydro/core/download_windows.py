"""
OpenHydro download windows (IDM style)
A small window per download started on this PC: live progress, pause/resume and cancel, then open
file / open folder once it completes. The page is web/download.html, served by the local API.
"""

import threading
from typing import Callable, Dict, Optional
from urllib.parse import quote

WIDTH, HEIGHT = 500, 380
# Shown above the browser the download came from, then a normal window again
ON_TOP_SECONDS = 2.0


class _PageApi:
    """Exposed to the page as window.pywebview.api (underscore attributes stay private)."""

    def __init__(self):
        self._window = None

    def close(self):
        if self._window:
            self._window.destroy()

    def set_title(self, title: str):
        if self._window:
            self._window.set_title(str(title)[:200])


class DownloadWindows:
    def __init__(self, base_url: str, create_window: Optional[Callable] = None):
        self.base_url = base_url.rstrip("/")
        self._create_window = create_window  # webview.create_window; injected by the tests
        self._windows: Dict[str, object] = {}
        self._lock = threading.Lock()

    def is_open(self, task_id: str) -> bool:
        return task_id in self._windows

    def open(self, task_id: str, filename: str):
        """Opens the window for this download, or brings its existing one forward."""
        with self._lock:
            existing = self._windows.get(task_id)
            if task_id in self._windows:
                if existing is not None:
                    try:
                        existing.show()
                        existing.restore()
                    except Exception:
                        pass
                return
            self._windows[task_id] = None  # reserved: a second request while it is being created does nothing
        # pywebview creates the window synchronously; keep that off the server's event loop
        threading.Thread(target=self._create, args=(task_id, filename), daemon=True).start()

    def _create(self, task_id: str, filename: str):
        try:
            create = self._create_window
            if create is None:
                import webview
                create = webview.create_window
            api = _PageApi()
            window = create(
                title=filename or "OpenHydro",
                url=f"{self.base_url}/static/download.html?task={quote(task_id)}",
                width=WIDTH,
                height=HEIGHT,
                resizable=False,
                on_top=True,
                background_color="#0b0f19",
                js_api=api,
            )
        except Exception as e:
            print(f"[DownloadWindow] {e}")
            self._windows.pop(task_id, None)
            return
        if window is None:
            self._windows.pop(task_id, None)
            return
        api._window = window
        self._windows[task_id] = window
        window.events.closed += lambda: self._windows.pop(task_id, None)
        threading.Timer(ON_TOP_SECONDS, self._release_on_top, args=(window,)).start()

    @staticmethod
    def _release_on_top(window):
        try:
            window.on_top = False
        except Exception:
            pass
