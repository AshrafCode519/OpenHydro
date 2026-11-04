"""
OpenHydro "Start with Windows"
The same HKCU Run entry the installer's "start with Windows" option writes, so the setting can be
changed from either place. Only meaningful for the installed (frozen) app on Windows.
"""

import sys
from typing import Optional

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "OpenHydro"


def supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _command() -> str:
    # Starts minimized to the tray; opening OpenHydro later shows its window (single instance)
    return f'"{sys.executable}" --headless'


def is_enabled() -> Optional[bool]:
    """True/False, or None when not supported (development run, other OS)."""
    if not supported():
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool):
    if not supported():
        raise RuntimeError("التشغيل مع ويندوز متاح في النسخة المثبتة فقط")
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
