"""
OpenHydro Download Task and Chunk Data Models
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    CONNECTING = "connecting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Chunk:
    id: int
    start_byte: int
    end_byte: int
    current_byte: int
    speed: float = 0.0  # bytes per second
    status: str = "idle"  # idle, downloading, completed, failed
    active_worker_id: Optional[str] = None

    @property
    def total_bytes(self) -> int:
        return max(0, self.end_byte - self.start_byte + 1)

    @property
    def downloaded_bytes(self) -> int:
        if self.status == "completed":
            return self.total_bytes
        raw = self.current_byte - self.start_byte
        return max(0, min(self.total_bytes, raw))

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.end_byte - self.current_byte + 1)

    @property
    def progress_percent(self) -> float:
        total = self.total_bytes
        if total <= 0:
            return 100.0 if self.status == "completed" else 0.0
        return min(100.0, (self.downloaded_bytes / total) * 100.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "current_byte": self.current_byte,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "remaining_bytes": self.remaining_bytes,
            "progress_percent": round(self.progress_percent, 2),
            "speed": round(self.speed, 2),
            "status": self.status,
            "active_worker_id": self.active_worker_id,
        }


@dataclass
class DownloadTask:
    url: str
    filename: str = ""
    save_path: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    total_bytes: int = 0
    supports_ranges: bool = False
    num_connections: int = 8
    status: TaskStatus = TaskStatus.QUEUED
    chunks: List[Chunk] = field(default_factory=list)
    speed: float = 0.0  # Overall speed in bytes/sec
    eta_seconds: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    category: str = "other"  # video, audio, compressed, document, program, other
    custom_headers: Dict[str, str] = field(default_factory=dict)
    referrer: Optional[str] = None
    is_hls: bool = False
    hls_info: Optional[Dict[str, Any]] = None
    is_extractor: bool = False
    selected_format: Optional[str] = None
    is_torrent: bool = False
    torrent_gid: Optional[str] = None
    seeders: int = 0
    peers: int = 0
    # What the download is doing before/besides transferring (e.g. preparing ffmpeg); shown, not saved
    phase: Optional[str] = None

    @property
    def downloaded_bytes(self) -> int:
        if self.status == TaskStatus.COMPLETED and self.total_bytes > 0:
            return self.total_bytes
        if not self.chunks:
            return 0
        return min(self.total_bytes if self.total_bytes > 0 else float("inf"), sum(c.downloaded_bytes for c in self.chunks))

    @property
    def progress_percent(self) -> float:
        if self.status == TaskStatus.COMPLETED:
            return 100.0
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, (self.downloaded_bytes / self.total_bytes) * 100.0)

    @classmethod
    def determine_category_for_filename(cls, filename: str) -> str:
        name = filename.lower()
        if name.endswith(".torrent") or name.startswith("magnet:"):
            return "torrent"
        if any(name.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".ts"]):
            return "video"
        if any(name.endswith(ext) for ext in [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a"]):
            return "audio"
        if any(name.endswith(ext) for ext in [".zip", ".rar", ".7z", ".tar", ".gz", ".iso"]):
            return "compressed"
        if any(name.endswith(ext) for ext in [".pdf", ".docx", ".doc", ".xlsx", ".pptx", ".txt", ".epub"]):
            return "document"
        if any(name.endswith(ext) for ext in [".exe", ".msi", ".apk", ".deb", ".rpm", ".dmg"]):
            return "program"
        return "other"

    def determine_category(self) -> str:
        if self.is_torrent or self.url.startswith("magnet:"):
            return "torrent"
        return self.determine_category_for_filename(self.filename)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "save_path": self.save_path,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "progress_percent": round(self.progress_percent, 2),
            "supports_ranges": self.supports_ranges,
            "num_connections": self.num_connections,
            "status": self.status.value,
            "speed": round(self.speed, 2),
            "eta_seconds": self.eta_seconds,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "category": self.category,
            "is_hls": self.is_hls,
            "is_extractor": self.is_extractor,
            "selected_format": self.selected_format,
            "is_torrent": self.is_torrent,
            "torrent_gid": self.torrent_gid,
            "seeders": self.seeders,
            "peers": self.peers,
            "phase": self.phase,
            "chunks": [c.to_dict() for c in self.chunks],
        }
