"""
Headless Edge/Chrome for the dashboard UI tests, driven over the DevTools protocol
No extra dependency: the websockets package the server already uses. Also serves the dashboard
for a test engine on a free port.
"""

import base64
import itertools
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from typing import Any, List, Optional

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
]

# Screenshots and timing checks must not depend on where an animation happened to be
NO_ANIMATIONS = """
  const style = document.createElement('style');
  style.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
  document.head.append(style);
"""


def find_browser() -> Optional[str]:
    configured = os.environ.get("OPENHYDRO_BROWSER")
    if configured:
        return configured
    for path in BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_json(url: str) -> Any:
    # No proxy: a system proxy must not intercept the browser's local debugging endpoint
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=2) as resp:
        return json.loads(resp.read())


class Browser:
    def __init__(self, path: str, width: int = 1280, height: int = 800):
        from websockets.sync.client import connect

        self.profile = tempfile.mkdtemp(prefix="openhydro-browser-")
        port = free_port()
        self.proc = subprocess.Popen(
            [path, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
             f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        page = None
        deadline = time.monotonic() + 20
        while page is None:
            try:
                page = next((t for t in _get_json(f"http://127.0.0.1:{port}/json") if t["type"] == "page"), None)
            except OSError:
                pass
            if page is None:
                if time.monotonic() > deadline:
                    self.close()
                    raise RuntimeError("the browser did not open its debugging port")
                time.sleep(0.2)
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
        self._ids = itertools.count(1)
        self.events: List[dict] = []
        self.send("Runtime.enable")
        self.send("Page.enable")

    def send(self, method: str, **params) -> dict:
        msg_id = next(self._ids)
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def evaluate(self, expression: str) -> Any:
        result = self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise AssertionError(f"JavaScript error: {details.get('exception', {}).get('description') or details}")
        return result["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 10.0, message: str = "") -> Any:
        deadline = time.monotonic() + timeout
        while True:
            value = self.evaluate(expression)
            if value:
                return value
            if time.monotonic() > deadline:
                raise AssertionError(message or f"timed out waiting for: {expression}")
            time.sleep(0.05)

    def open(self, url: str, timeout: float = 20.0):
        # Waits for the new document's load event: a readyState check right after navigating can still
        # see the previous page, which then goes on handling events in the test's back
        self.events.clear()
        self.send("Page.navigate", url=url)
        deadline = time.monotonic() + timeout
        while not any(event["method"] == "Page.loadEventFired" for event in self.events):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"{url} did not load")
            # Only listen: a command sent mid-navigation can fail while the page changes process
            try:
                msg = json.loads(self.ws.recv(timeout=min(remaining, 1.0)))
            except TimeoutError:
                continue
            if "method" in msg:
                self.events.append(msg)
        self.wait_for("document.readyState === 'complete'")

    def js_errors(self) -> List[str]:
        """Uncaught exceptions and console.error calls since the page was opened."""
        self.evaluate("0")  # collects the events that arrived in the meantime
        errors = []
        for event in self.events:
            params = event.get("params", {})
            if event["method"] == "Runtime.exceptionThrown":
                details = params.get("exceptionDetails", {})
                errors.append(details.get("exception", {}).get("description") or details.get("text", "exception"))
            elif event["method"] == "Runtime.consoleAPICalled" and params.get("type") == "error":
                errors.append(" ".join(str(arg.get("value", arg.get("description", ""))) for arg in params.get("args", [])))
        return errors

    KEYS = {  # key: (code, Windows virtual key code, text typed)
        "ArrowDown": ("ArrowDown", 40, None), "ArrowUp": ("ArrowUp", 38, None), "Delete": ("Delete", 46, None),
        "Enter": ("Enter", 13, "\r"), "Escape": ("Escape", 27, None), " ": ("Space", 32, " "),
    }

    def press(self, key: str, ctrl: bool = False):
        """A real key press, through the browser's input pipeline (not a synthetic JS event)."""
        if key in self.KEYS:
            code, vk, text = self.KEYS[key]
        else:  # a letter
            code, vk, text = f"Key{key.upper()}", ord(key.upper()), key
        params = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": 2 if ctrl else 0}
        self.send("Input.dispatchKeyEvent", type="keyDown", **params, **({"text": text} if text and not ctrl else {}))
        self.send("Input.dispatchKeyEvent", type="keyUp", **params)

    def screenshot(self, path: str):
        data = self.send("Page.captureScreenshot", format="png")["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def seed_tasks(engine, folder: str) -> dict:
    """One task per state, with fixed numbers and times so the list always looks the same."""
    from openhydro.core import Chunk, DownloadTask, TaskStatus

    gib = 2 ** 30

    def task(name, status, total, done, created, **fields):
        t = DownloadTask(url=f"https://downloads.example.com/{name.replace(' ', '%20')}", filename=name,
                         save_path=os.path.join(folder, name), status=status, total_bytes=total,
                         created_at=1_700_000_000 + created, supports_ranges=True, **fields)
        t.category = t.determine_category()
        quarter = total // 4
        t.chunks = [Chunk(id=i, start_byte=i * quarter, end_byte=(i + 1) * quarter - 1,
                          current_byte=i * quarter + min(quarter, max(0, done - i * quarter)),
                          status="downloading" if status == TaskStatus.DOWNLOADING else "idle")
                    for i in range(4)] if status != TaskStatus.COMPLETED else []
        engine.tasks[t.id] = t
        return t

    return {
        "downloading": task("Ubuntu-24.04-desktop-amd64.iso", TaskStatus.DOWNLOADING, 6 * gib, 4 * gib, 4,
                            speed=12.5 * 2 ** 20, eta_seconds=380),
        "paused": task("Lecture 05 - Algorithms.mp4", TaskStatus.PAUSED, gib, int(0.4 * gib), 3),
        "error": task("dataset.zip", TaskStatus.ERROR, 2 * gib, gib // 2, 2, error_message="HTTP 403"),
        "completed": task("OpenHydro_Setup_v1.2.3.exe", TaskStatus.COMPLETED, 90 * 2 ** 20, 90 * 2 ** 20, 1),
    }


class DashboardServer:
    """The real app (create_app) for a test engine, in a background thread."""

    def __init__(self, engine, **app_options):
        import uvicorn
        from unittest import mock
        from openhydro.api import create_app
        from openhydro.core.updater import UpdateChecker

        # The page checks for updates on load: no GitHub request, and no update dialog in the way
        import openhydro
        self._no_update = mock.patch.object(UpdateChecker, "check_for_updates", mock.AsyncMock(return_value={
            "has_update": False, "current_version": openhydro.__version__, "latest_version": openhydro.__version__}))
        self._no_update.start()
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        app = create_app(engine, remote_token="ui-test", listening_on_network=False, **app_options)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the dashboard server did not start")
            time.sleep(0.05)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self._no_update.stop()
