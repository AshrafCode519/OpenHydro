"""
OpenHydro Application Settings
User preferences kept in the task store (survive restarts), validated on every change.
"""

import os
import json
import tempfile
from typing import Any, Dict, Optional

SETTINGS_KEY = "app_settings"

DEFAULTS: Dict[str, Any] = {
    "download_dir": None,          # None: the user's Downloads folder
    "default_connections": 8,
    "max_active_downloads": 3,
    "speed_limit_kbps": 0,         # 0: unlimited
    "auto_resume": True,           # downloads interrupted by closing the app continue on the next start
    "notify_on_complete": True,
    "clipboard_monitor": True,     # offer download links copied anywhere (never downloads without a click)
    "remote_access": False,        # phone/LAN control; off = the server only listens on this PC
}


class SettingsError(ValueError):
    """A rejected value; the message is shown to the user."""


def _bounded_int(value: Any, low: int, high: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SettingsError(f"{label} يجب أن يكون رقماً بين {low} و {high}")
    return value


def _flag(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsError(f"{label} يجب أن يكون true أو false")
    return value


def _writable_dir(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not os.path.isabs(value):
        raise SettingsError("مجلد الحفظ يجب أن يكون مساراً كاملاً (مثل D:\\Downloads)")
    path = os.path.normpath(value)
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError:
        raise SettingsError("لا يمكن الكتابة في هذا المجلد")
    return path


VALIDATORS = {
    "download_dir": _writable_dir,
    "default_connections": lambda v: _bounded_int(v, 1, 32, "عدد الاتصالات"),
    "max_active_downloads": lambda v: _bounded_int(v, 1, 10, "عدد التحميلات المتزامنة"),
    "speed_limit_kbps": lambda v: _bounded_int(v, 0, 10_000_000, "حد السرعة"),
    "auto_resume": lambda v: _flag(v, "الاستئناف التلقائي"),
    "notify_on_complete": lambda v: _flag(v, "إشعار الاكتمال"),
    "clipboard_monitor": lambda v: _flag(v, "مراقبة الحافظة"),
    "remote_access": lambda v: _flag(v, "التحكم عن بعد"),
}


class AppSettings:
    def __init__(self, store):
        self._store = store
        self._values = dict(DEFAULTS)
        try:
            saved = json.loads(store.get_setting(SETTINGS_KEY) or "{}")
        except ValueError:
            saved = {}
        for key, value in saved.items():
            if key in VALIDATORS:
                try:
                    self._values[key] = VALIDATORS[key](value)
                except SettingsError:
                    pass  # e.g. a download folder on a drive that is gone: keep the default

    def get(self, key: str) -> Any:
        return self._values[key]

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._values)

    def update(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Validates every value first, then applies and saves them all (or none)."""
        unknown = set(changes) - set(VALIDATORS)
        if unknown:
            raise SettingsError(f"إعدادات غير معروفة: {', '.join(sorted(unknown))}")
        validated = {key: VALIDATORS[key](value) for key, value in changes.items()}
        self._values.update(validated)
        self._store.set_setting(SETTINGS_KEY, json.dumps(self._values))
        return validated
