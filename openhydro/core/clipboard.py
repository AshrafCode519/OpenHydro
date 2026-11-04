"""
OpenHydro clipboard monitoring (IDM style)
A link to a file, or a magnet link, copied anywhere is offered in OpenHydro; nothing is downloaded
without the user's click. Only the cheap clipboard sequence number is polled, the text is read when
it changes, and content that password managers mark as private is never read.
"""

import sys
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

# The file types the browser extension captures (extension/background.js, CAPTURE_DEFAULTS)
DOWNLOAD_EXTENSIONS = {
    "exe", "msi", "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "dmg", "apk", "deb", "rpm",
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mp3", "m4a", "flac", "wav", "aac", "ogg",
    "pdf", "epub", "torrent",
}
MAX_LINK_LENGTH = 4096
MAX_TEXT_BYTES = 16 * 1024  # bigger clipboard text is not a single link: not even read
POLL_INTERVAL = 0.5


def link_filename(link: str) -> str:
    if link[:7].lower() == "magnet:":
        names = parse_qs(urlparse(link).query).get("dn")
        return names[0] if names else "Magnet"
    return unquote(urlparse(link).path.rsplit("/", 1)[-1])


def extract_download_link(text: Optional[str]) -> Optional[str]:
    """The copied text when it is one link to a downloadable file or a magnet link."""
    link = (text or "").strip()
    if not link or len(link) > MAX_LINK_LENGTH or any(c.isspace() for c in link):
        return None
    if link[:8].lower() == "magnet:?":
        return link if "xt=urn:" in link.lower() else None
    try:
        parsed = urlparse(link)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    name = link_filename(link)
    extension = name.rsplit(".", 1)[1].lower() if "." in name else ""
    return link if extension in DOWNLOAD_EXTENSIONS else None


class WindowsClipboard:
    """Read-only access to the Windows clipboard through user32."""

    CF_UNICODETEXT = 13
    # Formats password managers add to a copied secret so clipboard tools leave it alone
    PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        # Own WinDLL instances: the signatures set here do not leak into pystray/pywebview's windll
        self._ctypes = ctypes
        self._user32 = user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalSize.restype = ctypes.c_size_t
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        self._private_formats = [user32.RegisterClipboardFormatW(name) for name in self.PRIVATE_FORMATS]

    def sequence(self) -> int:
        return self._user32.GetClipboardSequenceNumber()

    def read_text(self) -> Optional[str]:
        """The clipboard text, or None: no text, marked private, too big, or held by another app."""
        user32, kernel32 = self._user32, self._kernel32
        if not user32.IsClipboardFormatAvailable(self.CF_UNICODETEXT):
            return None
        if any(fmt and user32.IsClipboardFormatAvailable(fmt) for fmt in self._private_formats):
            return None
        for _ in range(5):  # the app that just copied may still have the clipboard open
            if user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return None
        try:
            handle = user32.GetClipboardData(self.CF_UNICODETEXT)
            size = kernel32.GlobalSize(handle) if handle else 0
            if not size or size > MAX_TEXT_BYTES:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return self._ctypes.wstring_at(pointer, size // 2).split("\0", 1)[0]
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()


class ClipboardWatcher:
    """Offers copied download links; the latest offer waits until the user takes or dismisses it."""

    def __init__(self, engine, on_offer: Optional[Callable[[Dict[str, Any]], None]] = None,
                 clipboard=None, interval: float = POLL_INTERVAL):
        self.engine = engine
        self.on_offer = on_offer
        self.clipboard = clipboard
        self.interval = interval
        self._offer: Optional[Dict[str, Any]] = None
        self._last_sequence: Optional[int] = None
        self._stop = threading.Event()

    def start(self) -> bool:
        if self.clipboard is None:
            if sys.platform != "win32":
                return False
            self.clipboard = WindowsClipboard()
        # Whatever was copied before OpenHydro started is not offered
        self._last_sequence = self.clipboard.sequence()
        threading.Thread(target=self._run, name="clipboard-watcher", daemon=True).start()
        return True

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.check()
            except Exception as e:
                print(f"[Clipboard] {e}")

    def check(self):
        """One poll: offers the copied text if the clipboard changed and it is a new download link."""
        sequence = self.clipboard.sequence()
        if sequence == self._last_sequence:
            return
        self._last_sequence = sequence
        # Turned off: the clipboard is not read at all, and turning it on later does not offer old copies
        if not self.engine.settings.get("clipboard_monitor"):
            return
        link = extract_download_link(self.clipboard.read_text())
        if not link or self._known(link) or (self._offer or {}).get("url") == link:
            return
        self._offer = {"url": link, "filename": link_filename(link)}
        self.engine._notify("clipboard_link", self._offer)
        if self.on_offer:
            self.on_offer(self._offer)

    def _known(self, link: str) -> bool:
        return any(task.url == link for task in list(self.engine.tasks.values()))

    def current_offer(self) -> Optional[Dict[str, Any]]:
        """The waiting link, unless it was added since (for example captured by the browser extension)."""
        offer = self._offer
        if offer and self._known(offer["url"]):
            self._offer = offer = None
        return offer

    def dismiss(self):
        self._offer = None

    def request_open(self):
        """The notification was clicked: open the add dialog for the link, now or once the window loads."""
        offer = self._offer
        if offer:
            offer["open"] = True
            self.engine._notify("clipboard_open", offer)
