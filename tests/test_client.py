import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from radiation_pi.client import Client, DeliveryError
from radiation_pi.config import Config, load_config
from radiation_pi.messages import Heartbeat, Measurement


class ApiHandler(BaseHTTPRequestHandler):
    requests = []
    response_code = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.requests.append((self.path, dict(self.headers), json.loads(body)))
        self.send_response(self.response_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": 17}).encode())

    def log_message(self, format, *args):
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        ApiHandler.requests = []
        ApiHandler.response_code = 200
        self.config = Config(
            server_url=f"http://127.0.0.1:{self.server.server_port}",
            station_id=1,
            detector_ids=(4,),
            unit="uSv/h",
            measurement_interval_seconds=5,
            heartbeat_interval_seconds=30,
            request_timeout_seconds=2,
            outbox_path=Path("data/outbox.sqlite3"),
            token="test-device-token",
        )

    def test_measurement_request_and_repeat_use_same_identity(self):
        client = Client(self.config)
        message = Measurement(4, Decimal("0.546"), datetime(2026, 9, 23, tzinfo=timezone.utc))
        self.assertEqual(client.send_measurement(message), {"id": 17})
        self.assertEqual(client.send_measurement(message), {"id": 17})
        first_path, headers, body = ApiHandler.requests[0]
        self.assertEqual(first_path, "/api/measurements")
        self.assertEqual(headers["Authorization"], "Bearer test-device-token")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(body, {
            "detectorId": 4, "value": 0.546, "unit": "uSv/h",
            "messageId": message.message_id, "measuredAt": "2026-09-23T00:00:00Z",
        })
        self.assertNotIn("stationId", body)
        self.assertEqual(ApiHandler.requests[0][2], ApiHandler.requests[1][2])

    def test_rejects_unconfigured_detector_without_network(self):
        with self.assertRaisesRegex(ValueError, "not configured"):
            Client(self.config).send_measurement(
                Measurement(3, Decimal("0.1"), datetime.now(timezone.utc))
            )
        self.assertEqual(ApiHandler.requests, [])

    def test_heartbeat_with_optional_metrics(self):
        message = Heartbeat(cpu_temp="42.5", free_disk_gb="16", memory_percent="38")
        Client(self.config).send_heartbeat(message)
        path, _, body = ApiHandler.requests[0]
        self.assertEqual(path, "/api/heartbeat")
        self.assertEqual(body["messageId"], message.message_id)
        self.assertEqual(body["cpuTemp"], 42.5)
        self.assertEqual(body["freeDiskGb"], 16)
        self.assertEqual(body["memoryPercent"], 38)

    def test_http_error_classification(self):
        for status, retryable in ((400, False), (403, False), (409, False), (429, True), (503, True)):
            with self.subTest(status=status):
                ApiHandler.response_code = status
                with self.assertRaises(DeliveryError) as caught:
                    Client(self.config).send_heartbeat(Heartbeat())
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(caught.exception.retryable, retryable)

    def test_decimal_validation(self):
        with self.assertRaises(ValueError):
            Measurement(4, "0.1234", datetime.now(timezone.utc))
        with self.assertRaises(ValueError):
            Heartbeat(memory_percent="101")
        with self.assertRaises(ValueError):
            Measurement(4, 0.1, datetime.now(timezone.utc))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.toml"
        self.path.write_text(
            'server_url = "http://127.0.0.1:8080"\n'
            'station_id = 1\n'
            'detector_ids = [4]\n'
            'unit = "uSv/h"\n'
            'measurement_interval_seconds = 5\n'
            'heartbeat_interval_seconds = 30\n'
            'request_timeout_seconds = 10\n'
            'outbox_path = "data/outbox.sqlite3"\n',
            encoding="utf-8",
        )

    def test_token_is_read_from_environment_and_redacted(self):
        with patch.dict("os.environ", {"RADIATION_PI_TOKEN": "example-secret"}):
            config = load_config(self.path)
        self.assertEqual(config.detector_ids, (4,))
        self.assertEqual(config.token, "example-secret")
        self.assertNotIn("example-secret", repr(config))

    def test_non_loopback_http_is_rejected(self):
        text = self.path.read_text(encoding="utf-8").replace("127.0.0.1", "pi-server.example")
        self.path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            load_config(self.path, token="example-secret")

    def test_missing_token_is_rejected(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "RADIATION_PI_TOKEN"):
                load_config(self.path)


if __name__ == "__main__":
    unittest.main()
