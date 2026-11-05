"""
Shared test helpers for OpenHydro
Importing this module keeps the whole test run away from the user's real app data.
"""

import os
import sys
import tempfile

# Task database and secrets go to a throwaway folder, never %LOCALAPPDATA%\OpenHydro
os.environ.setdefault("OPENHYDRO_DATA_DIR", tempfile.mkdtemp(prefix="openhydro-test-data-"))

# Tests never download OpenHydro's ffmpeg (~90 MB from GitHub; CI has no ffmpeg of its own): a download
# that would need it behaves as offline. Tests of the download itself replace these with fakes.
from openhydro.core import ffmpeg_component  # noqa: E402


def _no_network(*args, **kwargs):
    raise OSError("tests do not download ffmpeg")


ffmpeg_component._get_json = _no_network
ffmpeg_component._open = _no_network

# Same console setup as run.py: Arabic log lines must not raise UnicodeEncodeError on a cp1252 pipe
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def make_engine(test_case, download_dir: str):
    """DownloadEngine with its own task database inside download_dir, closed when the test ends."""
    from openhydro.core import DownloadEngine

    engine = DownloadEngine(download_dir=download_dir, store_path=os.path.join(download_dir, "tasks.db"))
    test_case.addCleanup(engine.close)
    return engine
