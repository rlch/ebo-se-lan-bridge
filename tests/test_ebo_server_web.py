import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import ebo_server


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "web" / "assets").mkdir(parents=True)
        (root / "web" / "index.html").write_text("<!doctype html><title>EBO</title>")
        (root / "web" / "sw.js").write_text("self.skipWaiting()")
        (root / "web" / "assets" / "app-abc123.js").write_text("console.log(1)")
        (root / "secret.txt").write_text("nope")
        self.patch = mock.patch.object(ebo_server, "WEB_DIR", root / "web")
        self.patch.start()
        self.client = TestClient(ebo_server.app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_index_and_spa_fallback(self):
        for path in ("/", "/settings"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertIn("<title>EBO</title>", r.text)
            self.assertEqual(r.headers["cache-control"], "no-cache")

    def test_hashed_assets_are_immutable_and_sw_is_not(self):
        r = self.client.get("/assets/app-abc123.js")
        self.assertIn("immutable", r.headers["cache-control"])
        self.assertEqual(self.client.get("/sw.js").headers["cache-control"], "no-cache")

    def test_head_is_allowed(self):
        self.assertEqual(self.client.head("/sw.js").status_code, 200)

    def test_path_traversal_falls_back_to_index(self):
        r = self.client.get("/..%2Fsecret.txt")
        self.assertNotIn("nope", r.text)

    def test_api_routes_win_over_web_fallback(self):
        r = self.client.get("/info")
        self.assertEqual(r.headers["content-type"], "application/json")
        self.assertFalse(r.json()["connected"])


class DriveSocketTests(unittest.TestCase):
    def test_drive_messages_send_frames_and_disconnect_stops(self):
        sent = []
        fake = mock.Mock()
        fake.send_rdt.side_effect = sent.append
        with mock.patch.object(ebo_server, "bridge", fake):
            client = TestClient(ebo_server.app)
            with client.websocket_connect("/ws/drive") as ws:
                ws.send_json({"ly": 0.5, "rx": -2})
                ws.send_json({"ly": 0, "rx": 0})
        self.assertEqual(sent[0], ebo_server.motor_frame(ly=0.5, rx=-1.0))
        self.assertEqual(sent[-1], ebo_server.motor_frame())
        self.assertEqual(len(sent), 3)


if __name__ == "__main__":
    unittest.main()
