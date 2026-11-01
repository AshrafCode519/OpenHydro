"""
OpenHydro BitTorrent & Magnet Link Engine
Powered by high-performance embedded aria2 core with JSON-RPC telemetry.
Provides seamless downloading of .torrent files and magnet: links.
"""

import os
import sys
import time
import base64
import atexit
import asyncio
import subprocess
from typing import Dict, Optional, List, Any
from urllib.parse import urlparse, parse_qs, unquote
import httpx


class TorrentManager:
    def __init__(self, download_dir: str, rpc_port: int = 6812):
        self.download_dir = download_dir
        self.rpc_port = rpc_port
        self.rpc_url = f"http://127.0.0.1:{self.rpc_port}/jsonrpc"
        self._proc: Optional[subprocess.Popen] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()
        self.binary_path = self._locate_binary()
        atexit.register(self.stop)

    def _locate_binary(self) -> Optional[str]:
        """Finds aria2c executable in project bundle or system path."""
        candidates = []
        if getattr(sys, "frozen", False):
            base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            exe_dir = os.path.dirname(sys.executable)
            candidates += [
                os.path.join(exe_dir, "_internal", "openhydro", "bin", "aria2c.exe"),
                os.path.join(base_dir, "_internal", "openhydro", "bin", "aria2c.exe"),
                os.path.join(exe_dir, "openhydro", "bin", "aria2c.exe"),
                os.path.join(exe_dir, "bin", "aria2c.exe"),
                os.path.join(base_dir, "openhydro", "bin", "aria2c.exe"),
                os.path.join(base_dir, "bin", "aria2c.exe"),
            ]

        # Development directory
        curr_dir = os.path.dirname(__file__)
        candidates += [
            os.path.abspath(os.path.join(curr_dir, "..", "bin", "aria2c.exe")),
            os.path.abspath(os.path.join(curr_dir, "..", "..", "openhydro", "bin", "aria2c.exe")),
            os.path.abspath(os.path.join(curr_dir, "..", "bin", "aria2c")),
        ]

        for p in candidates:
            if os.path.exists(p):
                return p

        # Check system PATH
        import shutil
        sys_bin = shutil.which("aria2c")
        if sys_bin:
            return sys_bin

        return None

    def is_available(self) -> bool:
        return self.binary_path is not None and os.path.exists(self.binary_path)

    async def ensure_running(self) -> bool:
        """Starts background aria2 RPC daemon if not already responding."""
        if not self.is_available():
            print("[TorrentManager] Warning: aria2c binary not found.")
            return False

        if not self._http_client:
            self._http_client = httpx.AsyncClient(timeout=4.0)

        # Check if already running on rpc_port
        try:
            r = await self._http_client.post(self.rpc_url, json={
                "jsonrpc": "2.0", "id": "ping", "method": "aria2.getVersion"
            })
            if r.status_code == 200:
                return True
        except Exception:
            pass

        # Spawn aria2c in background
        try:
            cmd = [
                self.binary_path,
                "--enable-rpc=true",
                f"--rpc-listen-port={self.rpc_port}",
                "--rpc-listen-all=false",
                "--rpc-allow-origin-all=true",
                "--dir=" + self.download_dir,
                "--max-connection-per-server=16",
                "--split=16",
                "--min-split-size=1M",
                "--bt-enable-dht=true",
                "--enable-dht6=false",
                "--dht-listen-port=6881",
                "--bt-max-peers=100",
                "--seed-time=0",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--summary-interval=0",
                "--quiet=true",
            ]

            flags = 0
            if sys.platform.startswith("win"):
                flags = subprocess.CREATE_NO_WINDOW

            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags
            )

            # Wait briefly for RPC to be responsive
            for _ in range(15):
                await asyncio.sleep(0.2)
                try:
                    r = await self._http_client.post(self.rpc_url, json={
                        "jsonrpc": "2.0", "id": "check", "method": "aria2.getVersion"
                    })
                    if r.status_code == 200:
                        return True
                except Exception:
                    pass
            return False
        except Exception as e:
            print(f"[TorrentManager] Failed to launch aria2c: {e}")
            return False

    async def _call(self, method: str, params: List[Any] = None) -> Any:
        await self.ensure_running()
        if not self._http_client:
            self._http_client = httpx.AsyncClient(timeout=4.0)

        payload = {
            "jsonrpc": "2.0",
            "id": f"call_{int(time.time() * 1000)}",
            "method": method,
            "params": params or []
        }
        resp = await self._http_client.post(self.rpc_url, json=payload)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "aria2 RPC error"))
        return data.get("result")

    async def add_magnet(self, magnet_uri: str, save_dir: Optional[str] = None) -> str:
        """Adds magnet link to torrent downloader. Returns GID."""
        options = {}
        if save_dir:
            options["dir"] = save_dir
        gid = await self._call("aria2.addUri", [[magnet_uri], options])
        return gid

    async def add_torrent_file(self, file_path_or_bytes: Any, save_dir: Optional[str] = None) -> str:
        """Adds .torrent file to downloader. Returns GID."""
        if isinstance(file_path_or_bytes, str) and os.path.exists(file_path_or_bytes):
            with open(file_path_or_bytes, "rb") as f:
                content = f.read()
        else:
            content = file_path_or_bytes

        b64 = base64.b64encode(content).decode("ascii")
        options = {}
        if save_dir:
            options["dir"] = save_dir
        gid = await self._call("aria2.addTorrent", [b64, [], options])
        return gid

    async def tell_status(self, gid: str) -> Optional[Dict[str, Any]]:
        """Retrieves real-time status of a torrent download."""
        try:
            return await self._call("aria2.tellStatus", [gid])
        except Exception:
            return None

    async def pause(self, gid: str) -> bool:
        try:
            await self._call("aria2.pause", [gid])
            return True
        except Exception:
            return False

    async def resume(self, gid: str) -> bool:
        try:
            await self._call("aria2.unpause", [gid])
            return True
        except Exception:
            return False

    async def remove(self, gid: str) -> bool:
        try:
            await self._call("aria2.remove", [gid])
            return True
        except Exception:
            return False

    def stop(self):
        """Terminates background process cleanly."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        if self._http_client:
            try:
                asyncio.create_task(self._http_client.aclose())
            except Exception:
                pass
            self._http_client = None

    @staticmethod
    def parse_magnet_info(magnet_uri: str) -> Dict[str, Any]:
        """Extracts display name, info_hash, and trackers from magnet link."""
        parsed = urlparse(magnet_uri)
        qs = parse_qs(parsed.query)

        dn = qs.get("dn", ["Torrent Download"])[0]
        dn = unquote(dn)

        xt = qs.get("xt", [""])[0]
        info_hash = xt.replace("urn:btih:", "") if "urn:btih:" in xt else xt

        trackers = [unquote(t) for t in qs.get("tr", [])]
        xl = int(qs.get("xl", [0])[0]) if qs.get("xl") else 0

        return {
            "name": dn,
            "info_hash": info_hash,
            "trackers": trackers,
            "total_size": xl,
        }

