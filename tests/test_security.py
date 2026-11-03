"""
Security regression tests for OpenHydro
Covers the API access guard, update installer restrictions, aria2 RPC secret, and filename sanitization.
"""

import os
import shutil
import tempfile
import unittest

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from helpers import make_engine
from openhydro.core import DownloadTask, TaskStatus
from openhydro.core.security import sanitize_filename, is_within_directory, load_or_create_secret
from openhydro.core.updater import is_official_release_asset
from openhydro.core.torrent import TorrentManager
from openhydro.api import create_app

TOKEN = "test-remote-token"
OFFICIAL_ASSET = "https://github.com/AshrafCode519/OpenHydro/releases/download/v1.2.3/OpenHydro_Setup_v1.2.3.exe"


class TestSecurityHelpers(unittest.TestCase):
    def test_sanitize_filename_blocks_traversal(self):
        self.assertEqual(sanitize_filename("../../evil.exe"), ".._.._evil.exe")
        self.assertEqual(sanitize_filename("..\\..\\Startup\\evil.bat"), ".._.._Startup_evil.bat")
        self.assertEqual(sanitize_filename(".."), "download.bin")
        self.assertEqual(sanitize_filename("", "fallback.bin"), "fallback.bin")
        self.assertEqual(sanitize_filename(None, "Torrent_Download"), "Torrent_Download")
        self.assertEqual(sanitize_filename("video.mp4. "), "video.mp4")
        self.assertEqual(sanitize_filename("normal name.zip"), "normal name.zip")

    def test_is_within_directory(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(is_within_directory(td, td))
            self.assertTrue(is_within_directory(td, os.path.join(td, "Videos", "Playlist")))
            self.assertFalse(is_within_directory(td, os.path.join(td, "..")))
            self.assertFalse(is_within_directory(td, os.path.dirname(td)))

    def test_secret_is_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "secrets.json")
            first = load_or_create_secret("remote_token", path)
            self.assertGreaterEqual(len(first), 24)
            self.assertEqual(load_or_create_secret("remote_token", path), first)
            self.assertNotEqual(load_or_create_secret("aria2_rpc_secret", path), first)


class TestUpdateAssetValidation(unittest.TestCase):
    def test_only_official_release_assets(self):
        self.assertTrue(is_official_release_asset(OFFICIAL_ASSET))
        self.assertFalse(is_official_release_asset(None))
        self.assertFalse(is_official_release_asset("https://evil.example/OpenHydro_Setup.exe"))
        self.assertFalse(is_official_release_asset("http://github.com/AshrafCode519/OpenHydro/releases/download/v1/x.exe"))
        self.assertFalse(is_official_release_asset("https://github.com/attacker/OpenHydro/releases/download/v1/x.exe"))
        self.assertFalse(is_official_release_asset("https://github.com@evil.example/AshrafCode519/OpenHydro/releases/download/v1/x.exe"))
        self.assertFalse(is_official_release_asset(
            "https://github.com/AshrafCode519/OpenHydro/releases/download/../../../attacker/repo/releases/download/v1/x.exe"))
        self.assertFalse(is_official_release_asset(
            "https://github.com/AshrafCode519/OpenHydro/releases/download/%2e%2e/%2e%2e/attacker/x.exe"))
        self.assertFalse(is_official_release_asset("https://github.com/AshrafCode519/OpenHydro/releases/tag/v1.2.3"))


class TestTorrentRpcSecret(unittest.TestCase):
    def test_aria2_requires_secret(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = TorrentManager(td, rpc_secret="s3cret")
            mgr.binary_path = "aria2c"
            cmd = mgr._build_command()
            self.assertIn("--rpc-secret=s3cret", cmd)
            self.assertNotIn("--rpc-allow-origin-all=true", cmd)
            self.assertIn("--rpc-listen-all=false", cmd)

            payload = mgr._payload("aria2.tellStatus", ["gid1"])
            self.assertEqual(payload["params"], ["token:s3cret", "gid1"])
            self.assertEqual(mgr._payload("aria2.getVersion", authenticated=False)["params"], [])


class TestApiAccessGuard(unittest.TestCase):
    def setUp(self):
        self.download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.download_dir, ignore_errors=True)
        self.engine = make_engine(self, self.download_dir)
        self.app = create_app(self.engine, remote_token=TOKEN)
        self.local = TestClient(self.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def remote_client(self, **kwargs):
        return TestClient(self.app, base_url="http://192.168.1.5:8765", client=("192.168.1.20", 50000), **kwargs)

    def test_local_dashboard_and_extension_allowed(self):
        self.assertEqual(self.local.get("/api/version").status_code, 200)
        same_origin = self.local.get("/api/version", headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(same_origin.status_code, 200)
        extension = self.local.get("/api/version", headers={"Origin": "chrome-extension://abcdefghijklmnop"})
        self.assertEqual(extension.status_code, 200)

    def test_foreign_website_blocked(self):
        resp = self.local.post(
            "/api/settings/limiter",
            json={"max_kbps": 1},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.engine.rate_limiter.max_bytes_per_second, 0)

        preflight = self.local.options(
            "/api/tasks",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(preflight.status_code, 403)

    def test_dns_rebinding_host_blocked(self):
        rebinding = TestClient(self.app, base_url="http://evil.example:8765", client=("127.0.0.1", 50000))
        self.assertEqual(rebinding.get("/api/tasks").status_code, 403)

    def test_remote_device_requires_token(self):
        self.assertEqual(self.remote_client().get("/api/tasks").status_code, 401)
        self.assertEqual(self.remote_client().get("/api/tasks?token=wrong").status_code, 401)

        first_visit = self.remote_client().get(f"/api/tasks?token={TOKEN}")
        self.assertEqual(first_visit.status_code, 200)
        self.assertIn(f"openhydro_token={TOKEN}", first_visit.headers.get("set-cookie", ""))

        with_cookie = self.remote_client().get("/api/tasks", headers={"Cookie": f"openhydro_token={TOKEN}"})
        self.assertEqual(with_cookie.status_code, 200)
        with_header = self.remote_client().get("/api/tasks", headers={"X-OpenHydro-Token": TOKEN})
        self.assertEqual(with_header.status_code, 200)

    def test_websocket_guarded(self):
        # TestClient ignores base_url for WebSockets, so absolute URLs set the Host header
        local_ws = "ws://127.0.0.1:8765/ws"
        with self.assertRaises(WebSocketDisconnect):
            with self.local.websocket_connect(local_ws, headers={"Origin": "https://evil.example"}):
                pass
        with self.assertRaises(WebSocketDisconnect):
            with self.remote_client().websocket_connect("ws://192.168.1.5:8765/ws"):
                pass
        with self.local.websocket_connect(local_ws, headers={"Origin": "http://127.0.0.1:8765"}) as ws:
            self.assertEqual(ws.receive_json()["type"], "snapshot")
        remote_ws = f"ws://192.168.1.5:8765/ws?token={TOKEN}"
        with self.remote_client().websocket_connect(remote_ws, headers={"Origin": "http://192.168.1.5:8765"}) as ws:
            self.assertEqual(ws.receive_json()["type"], "snapshot")

    def test_remote_info_link_carries_token(self):
        data = self.local.get("/api/system/remote_info").json()
        self.assertTrue(data["url"].endswith(f"/?token={TOKEN}"))

    def test_save_dir_outside_download_dir_rejected(self):
        outside = os.path.dirname(os.path.abspath(self.download_dir))
        for endpoint in ("/api/tasks", "/api/remote/push"):
            resp = self.local.post(endpoint, json={"url": "https://example.com/a.exe", "save_dir": outside})
            self.assertEqual(resp.status_code, 400, endpoint)

    def test_update_endpoints_restricted(self):
        resp = self.local.post("/api/updates/download", json={"download_url": "https://evil.example/payload.exe"})
        self.assertEqual(resp.status_code, 400)

        # Arbitrary file paths are no longer accepted
        resp = self.local.post("/api/updates/install", json={"file_path": "C:\\Windows\\System32\\calc.exe"})
        self.assertEqual(resp.status_code, 422)

        payload = os.path.join(self.download_dir, "payload.exe")
        with open(payload, "wb") as f:
            f.write(b"MZ")
        rogue = DownloadTask(url="https://evil.example/payload.exe", filename="payload.exe",
                             save_path=payload, status=TaskStatus.COMPLETED)
        self.engine.tasks[rogue.id] = rogue
        resp = self.local.post("/api/updates/install", json={"task_id": rogue.id})
        self.assertEqual(resp.status_code, 403)

        unfinished = DownloadTask(url=OFFICIAL_ASSET, filename="setup.exe",
                                  save_path=payload, status=TaskStatus.DOWNLOADING)
        self.engine.tasks[unfinished.id] = unfinished
        resp = self.local.post("/api/updates/install", json={"task_id": unfinished.id})
        self.assertEqual(resp.status_code, 403)

    def test_storage_manager_endpoints_do_not_crash(self):
        # These endpoints raised NameError (missing StorageManager import) before the fix
        task = DownloadTask(url="https://example.com/a.mp4", filename="a.mp4",
                            save_path=os.path.join(self.download_dir, "a.mp4"))
        self.engine.tasks[task.id] = task
        self.assertEqual(self.local.post(f"/api/tasks/{task.id}/preview_external").status_code, 404)
        self.assertEqual(self.local.get(f"/api/tasks/{task.id}/preview_stream").status_code, 404)

        resp = self.local.post("/api/playlist/download", json={"playlist_title": "My List", "items": []})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(is_within_directory(self.download_dir, resp.json()["folder"]))


if __name__ == "__main__":
    unittest.main()
