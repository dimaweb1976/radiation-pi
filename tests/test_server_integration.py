"""Opt-in test against the isolated Spring Boot H2 server on port 18081."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from os import environ

from radiation_pi.client import Client, DeliveryError
from radiation_pi.config import Config
from radiation_pi.delivery import OutboxSender
from radiation_pi.messages import Heartbeat, Measurement
from radiation_pi.outbox import Outbox


BASE_URL = "http://127.0.0.1:18081"
ADMIN_TOKEN = "pi-integration-admin-token-only"
DEVICE_TOKEN = "pi-integration-device-token-only"


def api(method, path, payload=None, token=None):
    body = None if payload is None else json.dumps(payload, default=float).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            return error.code, None
        finally:
            error.close()


@unittest.skipUnless(environ.get("RADIATION_INTEGRATION_RUN") == "1",
                     "set RADIATION_INTEGRATION_RUN=1 for the isolated H2 server")
class ServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Empty DB and test-only credentials are required before any write.
        status, stations = api("GET", "/api/stations")
        if status != 200 or stations != []:
            raise RuntimeError("integration server must have an empty isolated database")
        status, own = api("POST", "/api/stations", {
            "name": "Pi Integration Station", "location": "Test", "ipAddress": None,
            "latitude": None, "longitude": None,
        }, ADMIN_TOKEN)
        if status != 200 or own["id"] != 1:
            raise RuntimeError("test-only administrator token or station 1 fixture is unavailable")
        status, other = api("POST", "/api/stations", {
            "name": "Other Integration Station", "location": "Test", "ipAddress": None,
            "latitude": None, "longitude": None,
        }, ADMIN_TOKEN)
        if status != 200 or other["id"] != 2:
            raise RuntimeError("second test station fixture could not be created")
        status, detector = api("POST", "/api/detectors", {
            "name": "Other Detector", "location": "Test", "type": "Gamma", "stationId": 2,
        }, ADMIN_TOKEN)
        if status != 200:
            raise RuntimeError("other station detector fixture could not be created")
        cls.other_detector_id = detector["id"]

    def setUp(self):
        name = "Integration Dose " + uuid4().hex[:8]
        status, detector = api("POST", "/api/detectors", {
            "name": name, "location": "Test", "type": "Gamma", "stationId": 1,
        }, ADMIN_TOKEN)
        self.assertEqual(status, 200)
        self.detector_id = detector["id"]
        self.detector_name = name
        self.config = Config(
            server_url=BASE_URL, station_id=1, detector_ids=(self.detector_id,),
            unit="uSv/h", measurement_interval_seconds=1,
            heartbeat_interval_seconds=1, request_timeout_seconds=5,
            outbox_path=Path("unused.sqlite3"), token=DEVICE_TOKEN,
        )

    def test_authentication_and_detector_ownership(self):
        message = Measurement(self.detector_id, "0.120", datetime.now(timezone.utc))
        status, _ = api("POST", "/api/measurements", message.payload())
        self.assertEqual(status, 403)
        status, _ = api("POST", "/api/detectors", {
            "name": "Forbidden", "type": "Gamma", "stationId": 1,
        }, DEVICE_TOKEN)
        self.assertEqual(status, 403)
        foreign_config = Config(
            **{**self.config.__dict__, "detector_ids": (self.other_detector_id,)}
        )
        with self.assertRaises(DeliveryError) as caught:
            Client(foreign_config).send_measurement(
                Measurement(self.other_detector_id, "0.120", datetime.now(timezone.utc))
            )
        self.assertEqual(caught.exception.status, 403)

    def test_threshold_transitions_and_duplicate_id(self):
        client = Client(self.config)
        for value in ("0.120", "0.600", "1.200", "0.120"):
            message = Measurement(self.detector_id, value, datetime.now(timezone.utc))
            saved = client.send_measurement(message)
            self.assertEqual(client.send_measurement(message)["id"], saved["id"])
        status, measurements = api("GET", f"/api/measurements/detector/{self.detector_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(measurements), 4)
        status, events = api("GET", "/api/events")
        self.assertEqual(status, 200)
        types = [event["eventType"] for event in reversed(events)
                 if self.detector_name in event["message"]]
        self.assertEqual(types, ["RADIATION_WARNING", "RADIATION_ALARM", "RADIATION_NORMAL"])

    def test_heartbeat_deduplication(self):
        heartbeat = Heartbeat(cpu_temp="42.0", free_disk_gb="16.0", memory_percent="38.0")
        client = Client(self.config)
        first = client.send_heartbeat(heartbeat)
        second = client.send_heartbeat(heartbeat)
        self.assertEqual(first["id"], second["id"])
        status, rows = api("GET", "/api/heartbeat")
        self.assertEqual(status, 200)
        self.assertEqual(sum(row["messageId"] == heartbeat.message_id for row in rows), 1)
        status, stations = api("GET", "/api/stations")
        self.assertEqual(status, 200)
        self.assertEqual(stations[0]["status"], "ONLINE")

    def test_outbox_replays_same_body_after_lost_response(self):
        class FailOnceClient:
            def __init__(self, real):
                self.real = real
                self.first = True

            def serialize_measurement(self, message):
                return self.real.serialize_measurement(message)

            def send_serialized(self, kind, body):
                if self.first:
                    self.first = False
                    # The server commits the message but the client loses its response.
                    self.real.send_serialized(kind, body)
                    raise DeliveryError("response lost", retryable=True)
                return self.real.send_serialized(kind, body)

        message = Measurement(self.detector_id, "0.234", datetime.now(timezone.utc))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            with Outbox(path, station_id=1, server_url=BASE_URL) as outbox:
                sender = OutboxSender(FailOnceClient(Client(self.config)), outbox,
                                      clock=lambda: 100)
                self.assertEqual(sender.send_measurement(message)["event"], "retry_scheduled")
                self.assertEqual(len(outbox.summary()), 1)
            with Outbox(path, station_id=1, server_url=BASE_URL) as outbox:
                events = OutboxSender(Client(self.config), outbox,
                                      clock=lambda: 102).flush()
                self.assertEqual(events[0]["event"], "sent")
                self.assertEqual(outbox.summary(), [])
        status, rows = api("GET", f"/api/measurements/detector/{self.detector_id}")
        self.assertEqual(status, 200)
        self.assertEqual([row["messageId"] for row in rows], [message.message_id])


if __name__ == "__main__":
    unittest.main()
