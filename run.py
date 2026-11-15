"""
OpenHydro Desktop Application Entry Point
Starts the high-performance core engine, the FastAPI server, and launches the native desktop window.
Handles IDM-style close-to-tray behavior: clicking 'X' minimizes to system tray next to the Windows clock,
and right-clicking the tray icon shows 'Exit' (خروج نهائي) to completely terminate the application.
"""

import sys
import os
import time
import socket
import threading
import argparse
import webbrowser

LOG_MAX_BYTES = 2 * 1024 * 1024  # then the log starts over, keeping the previous one as openhydro.log.1


class TimestampedLog:
    """openhydro.log: each line starts with when it was written (it had no times, so no order of events)."""

    def __init__(self, stream):
        self._stream = stream
        self._line_start = True
        self._lock = threading.Lock()

    def write(self, text):
        with self._lock:
            out = []
            for piece in text.splitlines(keepends=True):
                if self._line_start:
                    out.append(time.strftime("[%Y-%m-%d %H:%M:%S] "))
                out.append(piece)
                self._line_start = piece.endswith(("\n", "\r"))
            self._stream.write("".join(out))
        return len(text)

    def flush(self):
        self._stream.flush()

    def isatty(self):
        return False

    def writable(self):
        return True

    @property
    def encoding(self):
        return self._stream.encoding


LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", ".")), "OpenHydro")


def open_log(log_dir: str) -> TimestampedLog:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "openhydro.log")
    # It was only ever appended to: start over past a size, keeping one previous file
    if os.path.exists(log_path) and os.path.getsize(log_path) > LOG_MAX_BYTES:
        os.replace(log_path, log_path + ".1")
    return TimestampedLog(open(log_path, "a", encoding="utf-8", buffering=1))


def remove_leftover_profiles(log_dir: str, temp_dir: str = None) -> int:
    """
    Up to 1.3.2 the window used a private WebView2 profile that pywebview creates in TEMP and often fails
    to delete on exit (5-10 MB each time), writing "Failed to delete user data folder: ...'<TEMP>\\tmpXXXX\\
    EBWebView\\...'" to this log. Those folders, and only those (certainly OpenHydro's), are removed.
    """
    import re
    import shutil
    import tempfile
    temp_dir = os.path.normcase(os.path.abspath(temp_dir or tempfile.gettempdir()))
    removed = 0
    for name in ("openhydro.log", "openhydro.log.1"):
        try:
            with open(os.path.join(log_dir, name), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for quoted in re.findall(r"Failed to delete user data folder: .*?'([^']+)'", text):
            head, found, _ = quoted.replace("\\\\", "\\").partition("\\EBWebView")
            folder = os.path.abspath(head)
            if (not found or os.path.normcase(os.path.dirname(folder)) != temp_dir
                    or not os.path.basename(folder).lower().startswith("tmp")
                    or not os.path.isdir(os.path.join(folder, "EBWebView"))):
                continue
            shutil.rmtree(folder, ignore_errors=True)
            removed += not os.path.exists(folder)
    return removed


# Ensure robust file logging if running in windowless mode (console=False)
if sys.stdout is None or sys.stderr is None:
    try:
        log_fp = open_log(LOG_DIR)
        if sys.stdout is None:
            sys.stdout = log_fp
        if sys.stderr is None:
            sys.stderr = log_fp
    except Exception:
        pass

# Ensure utf-8 encoding on Windows console
if sys.platform == "win32":
    try:
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # Set explicit AppUserModelID so Windows taskbar shows OpenHydro app identity
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("openhydro.downloader.app.1.0")
    except Exception:
        pass

import asyncio
import uvicorn

from openhydro.core import DownloadEngine, TaskStatus, SystemTrayManager
import openhydro
from openhydro.core import ytdlp_updater
from openhydro.core.instance import InstanceLock, ask_running_instance_to_show
from openhydro.core.clipboard import ClipboardWatcher
from openhydro.core.download_windows import DownloadWindows
from openhydro.core.security import get_app_data_dir
from openhydro.api import create_app, websocket_implementation

# OPENHYDRO_PORT only exists for development/testing next to an installed copy
PORT = int(os.environ.get("OPENHYDRO_PORT", "8765"))
LOCAL_URL = f"http://127.0.0.1:{PORT}"


def cleanup(engine: DownloadEngine, tray: SystemTrayManager = None):
    print("\n[OpenHydro] Saving states and shutting down gracefully...")
    if tray:
        tray.stop()
    if hasattr(engine, "torrent_manager") and engine.torrent_manager:
        try:
            engine.torrent_manager.stop()
        except Exception:
            pass
    for task in list(engine.tasks.values()):
        if task.status == TaskStatus.DOWNLOADING:
            # Interrupted by closing the app (not paused by the user): back in the queue,
            # so it continues on the next start when auto-resume is on
            task.status = TaskStatus.QUEUED
            engine.store.save(task)


def wait_for_server(host: str, port: int, timeout: float = 12.0) -> bool:
    """Polls the local TCP port until the server is confirmed listening and accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            time.sleep(0.15)
    return False


def start_server(engine: DownloadEngine, bind_host: str, on_show_window=None, clipboard=None,
                 on_download_started=None):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = create_app(engine, on_show_window=on_show_window, listening_on_network=bind_host != "127.0.0.1",
                         clipboard=clipboard, on_download_started=on_download_started)
        config = uvicorn.Config(
            app,
            host=bind_host,
            port=PORT,
            log_level="warning",
            access_log=False,
            http="h11",
            ws=websocket_implementation(),
        )
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())
    except Exception as e:
        import traceback
        err_msg = f"[Fatal] Uvicorn server failed: {e}\n{traceback.format_exc()}"
        print(err_msg)
        try:
            log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", ".")), "OpenHydro")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "openhydro_crash.log"), "a", encoding="utf-8") as f:
                f.write(err_msg + "\n")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="OpenHydro Modern Download Manager")
    parser.add_argument("--headless", action="store_true", help="Run without native GUI window (Headless Server mode)")
    parser.add_argument("--browser", action="store_true", help="Launch in default web browser instead of native desktop window")
    args = parser.parse_args()

    # One OpenHydro per user: a second launch (e.g. the desktop icon while the autostarted
    # --headless copy runs) hands over to the running instance instead of starting another engine
    instance = InstanceLock()
    if not instance.acquired:
        print("[OpenHydro] Already running: asking the running instance to show its window.")
        if not args.headless:
            ask_running_instance_to_show(LOCAL_URL)
        return

    # Before anything imports yt_dlp: prefer a newer copy downloaded by an earlier run
    ytdlp_version = ytdlp_updater.activate(openhydro.__version__)
    if ytdlp_version:
        print(f"[OpenHydro] Using updated yt-dlp {ytdlp_version}")

    engine = DownloadEngine()
    # Only this PC can reach the server unless phone/LAN control is turned on in the settings
    bind_host = "0.0.0.0" if engine.settings.get("remote_access") else "127.0.0.1"

    print("=" * 60)
    print("[OpenHydro] Modern Dynamic Download Manager")
    print("[OpenHydro] Dynamic Segmentation Engine: Active")
    print(f"[OpenHydro] Server listening on: {bind_host}:{PORT} (Local: {LOCAL_URL})")
    print("=" * 60)
    url = LOCAL_URL

    # State tracking for close-to-tray and clean exit
    should_really_quit = False
    window_holder = [None]
    tray_notified = [False]
    # Set when a window is wanted while running headless; pywebview must start on the main thread
    window_requested = threading.Event()
    # pywebview's GUI loop is running: only then can more windows be opened (before it, they would be
    # queued and pop up later)
    gui_running = threading.Event()
    download_windows = DownloadWindows(LOCAL_URL)

    def show_download_window(task):
        if gui_running.is_set() and engine.settings.get("show_download_window"):
            download_windows.open(task.id, task.filename)

    def show_window_action():
        w = window_holder[0]
        if w:
            try:
                w.show()
                w.restore()
            except Exception as e:
                print(f"[Tray] Error showing window: {e}")
        elif args.headless:
            window_requested.set()
        else:
            webbrowser.open(url)

    def exit_application_action():
        nonlocal should_really_quit
        should_really_quit = True
        print("[OpenHydro] Exit requested from system tray. Terminating gracefully...")
        cleanup(engine, tray)
        w = window_holder[0]
        if w:
            try:
                w.destroy()
            except Exception:
                pass
        time.sleep(0.2)
        os._exit(0)

    # Start System Tray Icon
    tray = SystemTrayManager(
        engine,
        on_show_window=show_window_action,
        on_exit=exit_application_action,
    )
    tray.start()

    def notify_finished(event_type, payload):
        if event_type == "task_completed" and engine.settings.get("notify_on_complete"):
            tray.notify(f"✅ اكتمل التنزيل: {payload.get('filename', '')}", "OpenHydro")
        elif event_type == "add_failed":
            # Sent from the browser (its menu, video button): the window may well be hidden
            tray.notify(f"⚠️ {payload.get('error', '')}", "تعذر إضافة التنزيل")

    engine.register_listener(notify_finished)

    def offer_copied_link(offer):
        # Clicking the notification opens the add dialog with the link (clipboard monitoring)
        tray.notify(f"📋 {offer['filename'][:120]}\nاضغط هنا لتنزيله", "رابط تنزيل في الحافظة",
                    on_click=lambda: (show_window_action(), clipboard.request_open()))

    clipboard = ClipboardWatcher(engine, on_offer=offer_copied_link)

    # Start FastAPI server in a dedicated thread
    server_thread = threading.Thread(target=start_server, daemon=True,
                                     args=(engine, bind_host, show_window_action, clipboard, show_download_window))
    server_thread.start()

    # Actively wait until the FastAPI TCP server is confirmed UP and responding!
    server_ready = wait_for_server("127.0.0.1", PORT, timeout=12.0)
    if not server_ready:
        print("[OpenHydro] Warning: Server socket did not bind within timeout.")

    # Loads yt-dlp in the background (faster first video probe) and fetches a newer one daily
    ytdlp_updater.start_background(openhydro.__version__)
    # WebView2 profiles earlier versions left in TEMP (they are named in this log)
    threading.Thread(target=remove_leftover_profiles, args=(LOG_DIR,), daemon=True).start()
    clipboard.start()

    def wait_until_interrupted():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            cleanup(engine, tray)
            print("[OpenHydro] Shutdown complete.")
            sys.exit(0)

    def wait_in_background():
        try:
            while not window_requested.wait(timeout=1):
                pass
        except KeyboardInterrupt:
            cleanup(engine, tray)
            print("[OpenHydro] Shutdown complete.")
            sys.exit(0)
        # Started at login without a window; the user has now opened OpenHydro
        print("[OpenHydro] Window requested: opening the desktop window.")
        run_native_window()

    def run_native_window(hidden=False):
        # Launch native Windows desktop GUI via pywebview with close-to-tray
        try:
            import webview
            print("[OpenHydro] Launching native desktop window...")
            w = webview.create_window(
                title="OpenHydro - مدير التنزيل العصري فائق السرعة",
                url=url,
                width=1100,
                height=720,
                min_size=(800, 600),
                background_color="#0b0f19",
                hidden=hidden and not window_requested.is_set(),
            )
            window_holder[0] = w

            def on_closing():
                if not should_really_quit:
                    print("[OpenHydro] Window close (X) intercepted -> Minimizing to System Tray.")
                    try:
                        w.hide()
                        if not tray_notified[0]:
                            tray.notify("OpenHydro مستمر في العمل في الخلفية بجوار الساعة.", "OpenHydro")
                            tray_notified[0] = True
                    except Exception as e:
                        print(f"[OpenHydro] Hide error: {e}")
                    return False
                return True

            w.events.closing += on_closing

            # A lasting profile in OpenHydro's folder, not a private one: pywebview creates that in TEMP and
            # often fails to delete it on exit (WebView2 still holds it), leaving 5-10 MB behind each time;
            # it also keeps what the pages remember (the download window's "when it finishes" choices).
            # The pages are served with no-cache, so a lasting cache never shows an old version.
            webview.start(gui_running.set, private_mode=False,
                          storage_path=os.path.join(get_app_data_dir(), "webview"))
            cleanup(engine, tray)
        except Exception as e:
            if hidden:
                print(f"[OpenHydro] No desktop window ({e}): staying in the background.")
                wait_in_background()
                return
            print(f"[OpenHydro] pywebview error: {e}. Falling back to default browser...")
            webbrowser.open(url)
            wait_until_interrupted()

    if args.headless:
        print("[OpenHydro] Running in headless mode. Press Ctrl+C to exit.")
        if sys.platform == "win32":
            # Started at login, in the tray: the window is created hidden so pywebview's loop runs and
            # download windows can open (IDM style); opening OpenHydro shows it
            run_native_window(hidden=True)
        else:
            wait_in_background()

    elif args.browser:
        print(f"[OpenHydro] Opening dashboard in browser: {url}")
        webbrowser.open(url)
        wait_until_interrupted()

    else:
        run_native_window()

if __name__ == "__main__":
    main()
