"""
Single-instance tests for OpenHydro
A second launch must find the running instance's lock and ask it (over the local API) to show its window.
"""

import os
import socket
import shutil
import tempfile
import threading
import unittest
import uuid
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer

from starlette.testclient import TestClient

from helpers import make_engine
from openhydro.core.instance import InstanceLock, ask_running_instance_to_show
from openhydro.api import create_app


class TestInstanceLock(unittest.TestCase):
    def test_second_holder_is_refused_until_release(self):
        name = f"OpenHydro.Test.{uuid.uuid4().hex}"
        first = InstanceLock(name)
        self.addCleanup(first.release)
        self.assertTrue(first.acquired)

        second = InstanceLock(name)
        self.assertFalse(second.acquired, "a second instance must see the lock is taken")
        second.release()

        first.release()
        third = InstanceLock(name)
        self.addCleanup(third.release)
        self.assertTrue(third.acquired, "the lock must be free once the first instance is gone")


class TestShowWindowEndpoint(unittest.TestCase):
    def setUp(self):
        download_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, download_dir, ignore_errors=True)
        self.engine = make_engine(self, download_dir)

    def client(self, on_show_window):
        app = create_app(self.engine, remote_token="t", on_show_window=on_show_window)
        return TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))

    def test_second_launch_brings_window_up(self):
        show = mock.Mock()
        resp = self.client(show).post("/api/app/show")
        self.assertEqual(resp.status_code, 200)
        show.assert_called_once()

    def test_without_window_handler(self):
        self.assertEqual(self.client(None).post("/api/app/show").status_code, 404)

    def test_websites_cannot_pop_the_window(self):
        show = mock.Mock()
        resp = self.client(show).post("/api/app/show", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status_code, 403)
        show.assert_not_called()


class _ShowHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.calls.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


class TestAskRunningInstance(unittest.TestCase):
    def test_posts_to_the_running_instance(self):
        server = HTTPServer(("127.0.0.1", 0), _ShowHandler)
        server.calls = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        self.assertTrue(ask_running_instance_to_show(f"http://127.0.0.1:{server.server_address[1]}"))
        self.assertEqual(server.calls, ["/api/app/show"])

    def test_gives_up_when_nothing_answers(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        self.assertFalse(ask_running_instance_to_show(f"http://127.0.0.1:{free_port}", attempts=1))


if __name__ == "__main__":
    unittest.main()
