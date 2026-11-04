"""
OpenHydro Windows System Tray Integration
Runs background tray icon next to the Windows clock with quick actions:
- Left-click / Double-click / Show: Restores & brings the OpenHydro window to the front.
- Right-click menu: Includes Pause All, Resume All, and Exit (خروج نهائي).
- Clicking a notification runs its action (by default: shows the window).
"""

import os
import threading
import webbrowser
from typing import Optional, Callable
from PIL import Image

try:
    import pystray
    from pystray import MenuItem as item
    PYSTRAY_AVAILABLE = True
except Exception:  # Not only ImportError: on headless Linux pystray fails to find a display
    PYSTRAY_AVAILABLE = False

# Sent by Windows when a notification from the tray icon is clicked; pystray itself ignores it
NIN_BALLOONUSERCLICK = 0x0405


class SystemTrayManager:
    def __init__(self, engine, on_show_window: Optional[Callable] = None, on_exit: Optional[Callable] = None):
        self.engine = engine
        self.on_show_window = on_show_window
        self.on_exit = on_exit
        self.icon = None
        self._thread = None
        self._notification_action: Optional[Callable] = None

    def _get_icon_image(self) -> Image.Image:
        candidates = []
        import sys
        if getattr(sys, "frozen", False):
            base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            candidates.append(os.path.join(base_dir, "assets", "icon.png"))
            candidates.append(os.path.join(base_dir, "_internal", "assets", "icon.png"))
            candidates.append(os.path.join(os.path.dirname(sys.executable), "assets", "icon.png"))
            candidates.append(os.path.join(os.path.dirname(sys.executable), "_internal", "assets", "icon.png"))
        candidates.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icon.png")))
        candidates.append(os.path.join(os.path.dirname(__file__), "..", "extension", "icons", "icon48.png"))

        for p in candidates:
            if os.path.exists(p):
                try:
                    return Image.open(p)
                except Exception:
                    pass
        # Fallback 16x16 cyan image
        return Image.new("RGBA", (16, 16), (6, 182, 212, 255))

    def _submit(self, coro):
        try:
            self.engine.submit(coro)
        except RuntimeError as e:
            print(f"[Tray] {e}")

    def notify(self, message: str, title: str = "OpenHydro", on_click: Optional[Callable] = None):
        """Displays a native Windows notification; clicking it runs on_click (default: show the window)."""
        if self.icon and hasattr(self.icon, "notify"):
            self._notification_action = on_click
            try:
                # Windows limits: longer text makes the notification fail entirely
                self.icon.notify(message[:255], title[:63])
            except Exception:
                pass

    def _route_notification_clicks(self):
        """Best effort, Windows only: relies on pystray's handler table, so it stays optional."""
        try:
            from pystray._util import win32
            handlers = self.icon._message_handlers
            original = handlers[win32.WM_NOTIFY]
        except Exception:
            return

        def on_notify(wparam, lparam):
            if lparam == NIN_BALLOONUSERCLICK:
                action = self._notification_action or self.on_show_window
                if action:
                    action()
                return 0
            return original(wparam, lparam)

        handlers[win32.WM_NOTIFY] = on_notify

    def start(self):
        if not PYSTRAY_AVAILABLE:
            return

        def show_action(icon=None, item=None):
            if self.on_show_window:
                self.on_show_window()
            else:
                webbrowser.open("http://127.0.0.1:8765")

        # Menu callbacks run on the tray thread: hand the work to the server's event loop
        def pause_all_action(icon, item):
            self._submit(self.engine.pause_all())

        def resume_all_action(icon, item):
            self._submit(self.engine.resume_all())

        def exit_action(icon, item):
            icon.stop()
            if self.on_exit:
                self.on_exit()

        menu = pystray.Menu(
            item("⚡ فتح البرنامج (Open OpenHydro)", show_action, default=True),
            pystray.Menu.SEPARATOR,
            item("▶ استئناف كافة التحميلات", resume_all_action),
            item("⏸ إيقاف كافة التحميلات مؤقتاً", pause_all_action),
            pystray.Menu.SEPARATOR,
            item("❌ خروج نهائي (Exit)", exit_action),
        )

        image = self._get_icon_image()
        self.icon = pystray.Icon("OpenHydro", image, "OpenHydro Download Manager", menu)
        self._route_notification_clicks()

        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
