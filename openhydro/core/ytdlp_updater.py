"""
OpenHydro yt-dlp Self-Update
Video sites (YouTube above all) change often and an outdated yt-dlp stops working, while the app
ships whatever yt-dlp existed at build time. Once a day this fetches the latest official wheels of
yt-dlp and its JavaScript challenge solver (yt-dlp-ejs) from PyPI, checks their SHA-256 and keeps
them in the app data folder; from the next start they are imported ahead of the bundled copies.

Layout under <app data>/yt-dlp:
    next/      freshly downloaded, promoted to current/ at the next start
    current/   yt_dlp/, yt_dlp_ejs/ and info.json ({"app_version": ..., "packages": {...}})
"""

import io
import os
import sys
import json
import time
import shutil
import hashlib
import zipfile
import threading
import importlib.abc
import importlib.machinery
import urllib.request
from typing import Dict, Optional, Tuple

from .security import get_app_data_dir

# PyPI project -> top-level import package
PACKAGES = {"yt-dlp": "yt_dlp", "yt-dlp-ejs": "yt_dlp_ejs"}
PYPI_JSON = "https://pypi.org/pypi/{project}/json"
CHECK_INTERVAL_SECONDS = 24 * 3600

_finders = []


def store_dir() -> str:
    path = os.path.join(get_app_data_dir(), "yt-dlp")
    os.makedirs(path, exist_ok=True)
    return path


def version_tuple(version: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _read_info(folder: str) -> Optional[Dict]:
    try:
        with open(os.path.join(folder, "info.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class _PreferredPackageFinder(importlib.abc.MetaPathFinder):
    """
    Serves one package (and its submodules) from `root`. It has to sit in sys.meta_path ahead of
    PyInstaller's importer: a sys.path entry alone never wins over modules frozen into the app.
    """

    def __init__(self, package: str, root: str):
        self.package = package
        self.root = root

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.package and not fullname.startswith(self.package + "."):
            return None
        if fullname == self.package:
            search = [self.root]
        else:
            parent = sys.modules.get(fullname.rpartition(".")[0])
            search = list(getattr(parent, "__path__", None) or [])
        return importlib.machinery.PathFinder.find_spec(fullname, search)


def activate(app_version: str) -> Optional[str]:
    """
    Makes the downloaded yt-dlp importable ahead of the bundled one. Call before anything imports
    yt_dlp. Returns the activated yt-dlp version, or None to keep the bundled copy.
    """
    root = store_dir()
    current, staged = os.path.join(root, "current"), os.path.join(root, "next")

    # Promote a completed download; current/ is never replaced while the app runs, since
    # yt-dlp keeps importing extractor modules lazily from it
    if _read_info(staged):
        shutil.rmtree(current, ignore_errors=True)
        os.replace(staged, current)

    info = _read_info(current)
    if not info:
        return None
    if info.get("app_version") != app_version:
        # A newer OpenHydro install brought its own, fresher yt-dlp
        shutil.rmtree(current, ignore_errors=True)
        return None

    for module in PACKAGES.values():
        if os.path.isdir(os.path.join(current, module)) and module not in sys.modules:
            finder = _PreferredPackageFinder(module, current)
            sys.meta_path.insert(0, finder)
            _finders.append(finder)
    return info.get("packages", {}).get("yt-dlp")


def _deactivate(root: str):
    """Falls back to the bundled copies after the downloaded ones failed to import."""
    for finder in _finders:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
    _finders.clear()
    for name in list(sys.modules):
        if name.split(".")[0] in PACKAGES.values():
            del sys.modules[name]
    shutil.rmtree(os.path.join(root, "current"), ignore_errors=True)


def _get_json(url: str) -> Dict:
    from .worker import shared_ssl_context
    with urllib.request.urlopen(url, timeout=30, context=shared_ssl_context()) as resp:
        return json.load(resp)


def _get_bytes(url: str) -> bytes:
    from .worker import shared_ssl_context
    with urllib.request.urlopen(url, timeout=120, context=shared_ssl_context()) as resp:
        return resp.read()


def _download_wheel(project: str) -> Tuple[str, bytes]:
    """Latest pure-Python wheel of `project`, verified against PyPI's SHA-256."""
    data = _get_json(PYPI_JSON.format(project=project))
    wheel = next(f for f in data["urls"] if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl"))
    payload = _get_bytes(wheel["url"])
    if hashlib.sha256(payload).hexdigest() != wheel["digests"]["sha256"]:
        raise ValueError(f"{wheel['filename']}: SHA-256 mismatch")
    return data["info"]["version"], payload


def latest_version(project: str = "yt-dlp") -> str:
    return _get_json(PYPI_JSON.format(project=project))["info"]["version"]


def download_update(app_version: str) -> Dict[str, str]:
    """Fetches the latest wheels into next/ (used from the next start). Returns their versions."""
    root = store_dir()
    staged = os.path.join(root, "next")
    building = staged + ".partial"
    shutil.rmtree(building, ignore_errors=True)
    os.makedirs(building)

    versions = {}
    for project, module in PACKAGES.items():
        version, payload = _download_wheel(project)
        with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
            members = [m for m in wheel.namelist() if m.startswith(module + "/")]
            if not members:
                raise ValueError(f"{project} wheel has no {module}/ package")
            wheel.extractall(building, members)
        versions[project] = version

    # info.json last: next/ only counts once everything is in place
    with open(os.path.join(building, "info.json"), "w", encoding="utf-8") as f:
        json.dump({"app_version": app_version, "packages": versions, "downloaded_at": time.time()}, f)
    shutil.rmtree(staged, ignore_errors=True)
    os.replace(building, staged)
    return versions


def _check_due(root: str) -> bool:
    try:
        with open(os.path.join(root, "last_check"), encoding="utf-8") as f:
            return time.time() - float(f.read().strip()) >= CHECK_INTERVAL_SECONDS
    except (OSError, ValueError):
        return True


def _mark_checked(root: str):
    with open(os.path.join(root, "last_check"), "w", encoding="utf-8") as f:
        f.write(str(time.time()))


def maintain(app_version: str):
    """
    Background routine: warms up (and verifies) the yt-dlp in use, then at most once a day
    downloads a newer one for the next start.
    """
    root = store_dir()
    try:
        import yt_dlp.version
    except Exception as e:
        if not _finders:
            print(f"[yt-dlp] Bundled yt-dlp cannot be imported: {e}")
            return
        print(f"[yt-dlp] Downloaded copy failed to load ({e}); using the bundled one")
        _deactivate(root)
        import yt_dlp.version

    running = yt_dlp.version.__version__
    try:
        import yt_dlp_ejs  # noqa: F401
        have_solver = True
    except Exception:
        have_solver = False

    if not _check_due(root):
        return
    try:
        latest = latest_version()
        _mark_checked(root)
        staged = _read_info(os.path.join(root, "next")) or {}
        if staged.get("packages", {}).get("yt-dlp") == latest:
            return  # already downloaded, waiting for the next start
        if version_tuple(latest) > version_tuple(running) or not have_solver:
            versions = download_update(app_version)
            print(f"[yt-dlp] Downloaded {versions}; used from the next start (running {running})")
    except Exception as e:
        print(f"[yt-dlp] Update check failed: {e}")


def start_background(app_version: str) -> threading.Thread:
    thread = threading.Thread(target=maintain, args=(app_version,), name="yt-dlp-updater", daemon=True)
    thread.start()
    return thread
