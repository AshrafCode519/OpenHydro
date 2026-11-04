"""
Settings tests for OpenHydro
Validation and persistence, how the engine and API apply them, phone control, and start-with-Windows.
"""

import os
import sys
import shutil
import tempfile
import unittest
import uuid
from unittest import mock

from starlette.testclient import TestClient

from helpers import make_engine
from openhydro.core import autostart
from openhydro.core.settings import AppSettings, SettingsError, DEFAULTS
from openhydro.core.storage import TaskStore
from openhydro.api import create_app


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.store = TaskStore(os.path.join(self.dir, "tasks.db"))
        self.addCleanup(self.store.close)


class TestAppSettings(SettingsTestCase):
    def test_defaults(self):
        settings = AppSettings(self.store)
        self.assertEqual(settings.as_dict(), DEFAULTS)
        self.assertFalse(settings.get("remote_access"), "phone control must be off until the user turns it on")

    def test_changes_survive_a_restart(self):
        AppSettings(self.store).update({"default_connections": 16, "auto_resume": False})
        reloaded = AppSettings(self.store)
        self.assertEqual(reloaded.get("default_connections"), 16)
        self.assertFalse(reloaded.get("auto_resume"))

    def test_one_bad_value_rejects_the_whole_change(self):
        settings = AppSettings(self.store)
        for bad in ({"default_connections": 64}, {"default_connections": "8"}, {"auto_resume": "yes"},
                    {"max_active_downloads": 0}, {"unknown_option": 1}, {"download_dir": "relative/folder"}):
            with self.assertRaises(SettingsError, msg=bad):
                settings.update({"notify_on_complete": False, **bad})
        self.assertTrue(AppSettings(self.store).get("notify_on_complete"), "nothing may be saved from a rejected change")

    def test_download_folder_is_created_and_checked(self):
        settings = AppSettings(self.store)
        folder = os.path.join(self.dir, "new", "downloads")
        settings.update({"download_dir": folder})
        self.assertTrue(os.path.isdir(folder))
        settings.update({"download_dir": ""})
        self.assertIsNone(settings.get("download_dir"), "empty means the Downloads folder")

        a_file = os.path.join(self.dir, "file.txt")
        open(a_file, "w").close()
        with self.assertRaises(SettingsError):
            settings.update({"download_dir": a_file})


class TestEngineUsesSettings(SettingsTestCase):
    def test_saved_values_apply_on_start(self):
        folder = os.path.join(self.dir, "my downloads")
        AppSettings(self.store).update({"download_dir": folder, "speed_limit_kbps": 500, "max_active_downloads": 5})
        self.store.close()

        from openhydro.core import DownloadEngine
        engine = DownloadEngine(store_path=os.path.join(self.dir, "tasks.db"))
        self.addCleanup(engine.close)
        self.assertEqual(engine.download_dir, folder)
        self.assertEqual(engine.rate_limiter.max_bytes_per_second, 500 * 1024)
        self.assertEqual(engine.scheduler.max_active_downloads, 5)


class TestDefaultConnections(unittest.IsolatedAsyncioTestCase):
    async def test_new_downloads_use_the_default(self):
        download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, download_dir, ignore_errors=True)
        engine = make_engine(self, download_dir)
        engine.settings.update({"default_connections": 4})
        probe = {"filename": "f.bin", "total_bytes": 10 * 2**20, "supports_ranges": True}
        with mock.patch.object(engine, "probe_url", mock.AsyncMock(return_value=probe)):
            default = await engine.add_download("https://example.com/f.bin", auto_start=False)
            explicit = await engine.add_download("https://example.com/g.bin", num_connections=16, auto_start=False)
        self.assertEqual(default.num_connections, 4)
        self.assertEqual(explicit.num_connections, 16)


class TestSettingsApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.engine = make_engine(self, self.dir)

    def client(self, listening_on_network=False, host="127.0.0.1", client_ip="127.0.0.1"):
        app = create_app(self.engine, remote_token="t", listening_on_network=listening_on_network)
        return TestClient(app, base_url=f"http://{host}:8765", client=(client_ip, 50000))

    def test_read_and_update(self):
        api = self.client()
        current = api.get("/api/settings").json()
        self.assertEqual(current["default_connections"], 8)
        self.assertIsNone(current["start_with_windows"], "not available outside the installed app")
        self.assertEqual(current["effective_download_dir"], self.engine.download_dir)

        resp = api.put("/api/settings", json={"speed_limit_kbps": 256, "max_active_downloads": 2})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.engine.rate_limiter.max_bytes_per_second, 256 * 1024)
        self.assertEqual(self.engine.scheduler.max_active_downloads, 2)

        folder = os.path.join(self.dir, "elsewhere")
        api.put("/api/settings", json={"download_dir": folder})
        self.assertEqual(self.engine.download_dir, folder)

    def test_invalid_value_is_a_400_and_changes_nothing(self):
        api = self.client()
        resp = api.put("/api/settings", json={"default_connections": 99, "notify_on_complete": False})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(self.engine.settings.get("notify_on_complete"))

    def test_start_with_windows_needs_the_installed_app(self):
        resp = self.client().put("/api/settings", json={"start_with_windows": True})
        self.assertEqual(resp.status_code, 400)

    def test_older_endpoints_persist_too(self):
        api = self.client()
        api.post("/api/settings/limiter", json={"max_kbps": 100})
        api.post("/api/settings/scheduler", json={"max_active": 4})
        self.assertEqual(self.engine.settings.get("speed_limit_kbps"), 100)
        self.assertEqual(self.engine.settings.get("max_active_downloads"), 4)

    def test_phone_control_is_off_by_default(self):
        phone = self.client(listening_on_network=True, host="192.168.1.5", client_ip="192.168.1.20")
        self.assertEqual(phone.get("/api/tasks?token=t").status_code, 403, "even with the token")

        self.engine.settings.update({"remote_access": True})
        self.assertEqual(phone.get("/api/tasks?token=t").status_code, 200)

        self.engine.settings.update({"remote_access": False})
        self.assertEqual(phone.get("/api/tasks?token=t").status_code, 403, "turning it off applies at once")

    def test_turning_phone_control_on_asks_for_a_restart(self):
        resp = self.client(listening_on_network=False).put("/api/settings", json={"remote_access": True}).json()
        self.assertTrue(resp["restart_required"])
        info = self.client(listening_on_network=False).get("/api/system/remote_info").json()
        self.assertTrue(info["enabled"])
        self.assertFalse(info["listening"])


@unittest.skipUnless(sys.platform == "win32", "Windows registry")
class TestStartWithWindows(unittest.TestCase):
    def setUp(self):
        # A throwaway key: the real Run key is never touched by the tests
        import winreg
        self.key = rf"Software\OpenHydroTest-{uuid.uuid4().hex}"
        for target, value in (("RUN_KEY", self.key), ("supported", lambda: True)):
            patcher = mock.patch.object(autostart, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.key))

    def test_toggle(self):
        autostart.set_enabled(True)
        self.assertTrue(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(False)  # turning off twice is fine


if __name__ == "__main__":
    unittest.main()
