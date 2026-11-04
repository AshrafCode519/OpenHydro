"""
yt-dlp self-update tests for OpenHydro
Uses a fake package and fake PyPI responses: the real yt_dlp in this process is never replaced.
"""

import io
import os
import sys
import json
import time
import shutil
import hashlib
import zipfile
import tempfile
import subprocess
import unittest
from unittest import mock

import helpers  # noqa: F401  (isolated app data)
from openhydro.core import ytdlp_updater

FAKE_PACKAGES = {"fake-dl": "fake_dl", "fake-dl-ejs": "fake_dl_ejs"}


def make_wheel(module: str, version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as wheel:
        wheel.writestr(f"{module}/__init__.py", f'VERSION = "{version}"\n')
        wheel.writestr(f"{module}/sub.py", 'ORIGIN = "downloaded"\n')
        wheel.writestr(f"{module}-{version}.dist-info/METADATA", "Name: x\n")
    return buffer.getvalue()


class UpdaterTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for target, value in (("store_dir", lambda: self.root), ("PACKAGES", FAKE_PACKAGES)):
            patcher = mock.patch.object(ytdlp_updater, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.unload_fakes)

        self.wheels = {project: make_wheel(module, "2099.1.1") for project, module in FAKE_PACKAGES.items()}
        self.digests = {project: hashlib.sha256(payload).hexdigest() for project, payload in self.wheels.items()}

    def unload_fakes(self):
        for finder in list(ytdlp_updater._finders):
            if finder in sys.meta_path:
                sys.meta_path.remove(finder)
        ytdlp_updater._finders.clear()
        for name in list(sys.modules):
            if name.split(".")[0] in FAKE_PACKAGES.values():
                del sys.modules[name]

    def fake_pypi(self):
        def get_json(url):
            project = url.split("/pypi/")[1].split("/")[0]
            return {"info": {"version": "2099.1.1"}, "urls": [
                {"packagetype": "sdist", "filename": f"{project}.tar.gz", "url": "sdist", "digests": {"sha256": "x"}},
                {"packagetype": "bdist_wheel", "filename": f"{project}-2099.1.1-py3-none-any.whl",
                 "url": project, "digests": {"sha256": self.digests[project]}},
            ]}
        return (mock.patch.object(ytdlp_updater, "_get_json", side_effect=get_json),
                mock.patch.object(ytdlp_updater, "_get_bytes", side_effect=lambda url: self.wheels[url]))


class TestDownloadAndActivate(UpdaterTestCase):
    def test_download_lands_in_next_and_is_used_from_the_next_start(self):
        get_json, get_bytes = self.fake_pypi()
        with get_json, get_bytes:
            versions = ytdlp_updater.download_update("1.2.3")
        self.assertEqual(versions, {"fake-dl": "2099.1.1", "fake-dl-ejs": "2099.1.1"})
        self.assertTrue(os.path.exists(os.path.join(self.root, "next", "fake_dl", "__init__.py")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "next", "fake_dl-2099.1.1.dist-info")),
                         "only the package itself is extracted")

        self.assertEqual(ytdlp_updater.activate("1.2.3"), None, "PACKAGES has no 'yt-dlp' key in this test")
        self.assertFalse(os.path.exists(os.path.join(self.root, "next")))
        import fake_dl
        import fake_dl.sub
        self.assertEqual(fake_dl.VERSION, "2099.1.1")
        self.assertEqual(fake_dl.sub.ORIGIN, "downloaded")
        self.assertTrue(fake_dl.__file__.startswith(os.path.join(self.root, "current")))

    def test_tampered_wheel_is_rejected(self):
        self.digests["fake-dl"] = "0" * 64
        get_json, get_bytes = self.fake_pypi()
        with get_json, get_bytes, self.assertRaises(ValueError):
            ytdlp_updater.download_update("1.2.3")
        self.assertFalse(os.path.exists(os.path.join(self.root, "next")))

    def test_copy_from_another_app_version_is_dropped(self):
        get_json, get_bytes = self.fake_pypi()
        with get_json, get_bytes:
            ytdlp_updater.download_update("1.2.3")
        self.assertIsNone(ytdlp_updater.activate("1.3.0"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "current")),
                         "a newer OpenHydro ships its own, fresher yt-dlp")
        self.assertEqual(ytdlp_updater._finders, [])


class TestPreferredFinder(UpdaterTestCase):
    def test_downloaded_copy_wins_over_the_bundled_one(self):
        bundled = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bundled, ignore_errors=True)
        os.makedirs(os.path.join(bundled, "fake_dl"))
        with open(os.path.join(bundled, "fake_dl", "__init__.py"), "w") as f:
            f.write('VERSION = "bundled"\n')
        sys.path.insert(0, bundled)
        self.addCleanup(sys.path.remove, bundled)

        updated = os.path.join(self.root, "current")
        with zipfile.ZipFile(io.BytesIO(self.wheels["fake-dl"])) as wheel:
            wheel.extractall(updated)
        finder = ytdlp_updater._PreferredPackageFinder("fake_dl", updated)
        sys.meta_path.insert(0, finder)
        ytdlp_updater._finders.append(finder)

        import fake_dl
        self.assertEqual(fake_dl.VERSION, "2099.1.1")


class TestDailyCheck(UpdaterTestCase):
    def test_checks_once_a_day_and_not_twice_for_the_same_version(self):
        with mock.patch.object(ytdlp_updater, "PACKAGES", {"yt-dlp": "yt_dlp"}), \
                mock.patch.object(ytdlp_updater, "latest_version", return_value="9999.1.1"), \
                mock.patch.object(ytdlp_updater, "download_update", return_value={"yt-dlp": "9999.1.1"}) as download:
            ytdlp_updater.maintain("1.2.3")
            self.assertEqual(download.call_count, 1)

            ytdlp_updater.maintain("1.2.3")  # same day
            self.assertEqual(download.call_count, 1)

            # A day later, with that version already waiting in next/
            with open(os.path.join(self.root, "last_check"), "w") as f:
                f.write(str(time.time() - 2 * ytdlp_updater.CHECK_INTERVAL_SECONDS))
            os.makedirs(os.path.join(self.root, "next"))
            with open(os.path.join(self.root, "next", "info.json"), "w") as f:
                json.dump({"app_version": "1.2.3", "packages": {"yt-dlp": "9999.1.1"}}, f)
            ytdlp_updater.maintain("1.2.3")
            self.assertEqual(download.call_count, 1)

    def test_up_to_date_downloads_nothing(self):
        import yt_dlp.version
        with mock.patch.object(ytdlp_updater, "latest_version", return_value=yt_dlp.version.__version__), \
                mock.patch.object(ytdlp_updater, "download_update") as download, \
                mock.patch.dict(sys.modules, {"yt_dlp_ejs": mock.Mock()}):
            ytdlp_updater.maintain("1.2.3")
        download.assert_not_called()


class TestImportOrder(unittest.TestCase):
    def test_app_modules_do_not_import_yt_dlp_before_activation(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = "import sys, openhydro.core, openhydro.api; print('yt_dlp' in sys.modules)"
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([repo, os.environ.get("PYTHONPATH", "")])}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=repo)
        self.assertEqual(out.stdout.strip(), "False", out.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
