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

# Ensure robust file logging if running in windowless mode (console=False)
if sys.stdout is None or sys.stderr is None:
    try:
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", ".")), "OpenHydro")
        os.makedirs(log_dir, exist_ok=True)
        log_fp = open(os.path.join(log_dir, "openhydro.log"), "a", encoding="utf-8", buffering=1)
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
from openhydro.api import create_app

# OPENHYDRO_PORT only exists for development/testing next to an installed copy
PORT = int(os.environ.get("OPENHYDRO_PORT", "8765"))
SERVER_BIND_HOST = "0.0.0.0"
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
            task.status = TaskStatus.PAUSED
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


def start_server(engine: DownloadEngine, on_show_window=None):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = create_app(engine, on_show_window=on_show_window)
        config = uvicorn.Config(
            app,
            host=SERVER_BIND_HOST,
            port=PORT,
            log_level="warning",
            access_log=False,
            http="h11",
            ws="websockets",
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

    print("=" * 60)
    print("[OpenHydro] Modern Dynamic Download Manager")
    print("[OpenHydro] Dynamic Segmentation Engine: Active")
    print(f"[OpenHydro] Server listening on: {SERVER_BIND_HOST}:{PORT} (Local: {LOCAL_URL})")
    print("=" * 60)

    engine = DownloadEngine()
    url = LOCAL_URL

    # State tracking for close-to-tray and clean exit
    should_really_quit = False
    window_holder = [None]
    tray_notified = [False]
    # Set when a window is wanted while running headless; pywebview must start on the main thread
    window_requested = threading.Event()

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

    # Start FastAPI server in a dedicated thread
    server_thread = threading.Thread(target=start_server, args=(engine, show_window_action), daemon=True)
    server_thread.start()

    # Actively wait until the FastAPI TCP server is confirmed UP and responding!
    server_ready = wait_for_server("127.0.0.1", PORT, timeout=12.0)
    if not server_ready:
        print("[OpenHydro] Warning: Server socket did not bind within timeout.")

    # Loads yt-dlp in the background (faster first video probe) and fetches a newer one daily
    ytdlp_updater.start_background(openhydro.__version__)

    def wait_until_interrupted():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            cleanup(engine, tray)
            print("[OpenHydro] Shutdown complete.")
            sys.exit(0)

    def run_native_window():
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

            webview.start()
            cleanup(engine, tray)
        except Exception as e:
            print(f"[OpenHydro] pywebview error: {e}. Falling back to default browser...")
            webbrowser.open(url)
            wait_until_interrupted()

    if args.headless:
        print("[OpenHydro] Running in headless mode. Press Ctrl+C to exit.")
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

    elif args.browser:
        print(f"[OpenHydro] Opening dashboard in browser: {url}")
        webbrowser.open(url)
        wait_until_interrupted()

    else:
        run_native_window()

if __name__ == "__main__":
    main()
