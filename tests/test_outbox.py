import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from radiation_pi.cli import main
from radiation_pi.client import Client, DeliveryError
from radiation_pi.config import Config
from radiation_pi.delivery import OutboxSender
from radiation_pi.messages import Heartbeat, Measurement
from radiation_pi.outbox import Outbox


class RecordingClient:
    def __init__(self, config, outcomes):
        self.real = Client(config)
        self.outcomes = iter(outcomes)
        self.requests = []

    def serialize_measurement(self, message):
        return self.real.serialize_measurement(message)

    def serialize_heartbeat(self, message):
        return self.real.serialize_heartbeat(message)

    def send_serialized(self, kind, body):
        self.requests.append((kind, body))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "outbox.sqlite3"
        self.config = Config(
            server_url="http://127.0.0.1:8080", station_id=1,
            detector_ids=(4,), unit="uSv/h",
            measurement_interval_seconds=5, heartbeat_interval_seconds=30,
            request_timeout_seconds=1, outbox_path=self.path, token="test-token",
        )

    def _open(self):
        return Outbox(self.path, station_id=1, server_url=self.config.server_url)

    def test_replay_after_restart_uses_exact_body_and_message_id(self):
        message = Measurement(4, "0.546", datetime(2026, 9, 23, tzinfo=timezone.utc))
        failed = RecordingClient(self.config, [DeliveryError("offline", retryable=True)])
        with self._open() as outbox:
            result = OutboxSender(failed, outbox, clock=lambda: 100).send_measurement(message)
            self.assertEqual(result["event"], "retry_scheduled")
            item = outbox.pending_head()
            self.assertEqual(item.message_id, message.message_id)
            self.assertEqual(item.body, failed.requests[0][1])
            self.assertEqual(item.attempts, 1)

        recovered = RecordingClient(self.config, [{"id": 23}])
        with self._open() as outbox:
            events = OutboxSender(recovered, outbox, clock=lambda: 102).flush()
            self.assertEqual(events[0]["event"], "sent")
            self.assertEqual(outbox.summary(), [])
        self.assertEqual(recovered.requests[0], failed.requests[0])
        self.assertEqual(json.loads(recovered.requests[0][1])["messageId"], message.message_id)

    def test_fifo_waits_for_oldest_retry(self):
        failed = RecordingClient(self.config, [DeliveryError("offline", retryable=True)])
        with self._open() as outbox:
            sender = OutboxSender(failed, outbox, clock=lambda: 100)
            first = Heartbeat(message_id="first")
            second = Heartbeat(message_id="second")
            self.assertEqual(sender.send_heartbeat(first)["event"], "retry_scheduled")
            self.assertEqual(sender.send_heartbeat(second)["event"], "pending")
            self.assertEqual(len(failed.requests), 1)
            recovered = RecordingClient(self.config, [{"id": 1}, {"id": 2}])
            events = OutboxSender(recovered, outbox, clock=lambda: 102).flush()
            self.assertEqual([event["messageId"] for event in events], ["first", "second"])
            self.assertEqual(outbox.summary(), [])

    def test_permanent_error_is_blocked_until_manual_release(self):
        with self._open() as outbox:
            failed = RecordingClient(self.config, [DeliveryError("conflict", retryable=False, status=409)])
            result = OutboxSender(failed, outbox, clock=lambda: 100).send_heartbeat(
                Heartbeat(message_id="blocked")
            )
            self.assertEqual(result["event"], "blocked")
            self.assertEqual(outbox.summary()[0]["last_status"], 409)
            self.assertEqual(OutboxSender(failed, outbox, clock=lambda: 102).flush(), [])
            self.assertTrue(outbox.retry_blocked("blocked"))
            recovered = RecordingClient(self.config, [{"id": 9}])
            events = OutboxSender(recovered, outbox, clock=lambda: 102).flush()
            self.assertEqual(events[0]["event"], "sent")
            self.assertEqual(outbox.summary(), [])

    def test_outbox_cannot_be_reused_for_other_station_or_server(self):
        with self._open():
            pass
        with self.assertRaisesRegex(ValueError, "station_id"):
            Outbox(self.path, station_id=2, server_url=self.config.server_url)
        with self.assertRaisesRegex(ValueError, "server_url"):
            Outbox(self.path, station_id=1, server_url="https://other.example")

    def test_manual_cli_persists_when_server_is_unreachable(self):
        config_path = Path(self.temp.name) / "config.toml"
        config_path.write_text(
            'server_url="http://127.0.0.1:1"\nstation_id=1\ndetector_ids=[4]\n'
            'unit="uSv/h"\nmeasurement_interval_seconds=5\n'
            'heartbeat_interval_seconds=30\nrequest_timeout_seconds=0.2\n'
            'outbox_path="data/outbox.sqlite3"\n', encoding="utf-8",
        )
        output = io.StringIO()
        with patch.dict("os.environ", {"RADIATION_PI_TOKEN": "test-token"}), contextlib.redirect_stdout(output):
            result = main(["--config", str(config_path), "send-measurement",
                           "--detector-id", "4", "--value", "0.123"])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["event"], "retry_scheduled")
        with Outbox(config_path.parent / "data/outbox.sqlite3", station_id=1,
                    server_url="http://127.0.0.1:1") as outbox:
            rows = outbox.summary()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["attempts"], 1)

    def test_retry_delay_grows_and_is_capped(self):
        failures = [DeliveryError("offline", retryable=True) for _ in range(11)]
        client = RecordingClient(self.config, failures)
        with self._open() as outbox:
            outbox.enqueue("heartbeat", "backoff", client.serialize_heartbeat(
                Heartbeat(message_id="backoff")
            ))
            now = 100.0
            delays = []
            for _ in failures:
                events = OutboxSender(client, outbox, clock=lambda: now).flush()
                self.assertEqual(events[0]["event"], "retry_scheduled")
                delays.append(events[0]["retryAt"] - now)
                now = events[0]["retryAt"]
            self.assertEqual(delays, [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300])


if __name__ == "__main__":
    unittest.main()
