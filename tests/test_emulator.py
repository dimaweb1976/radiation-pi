import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from radiation_pi.cli import main
from radiation_pi.client import DeliveryError
from radiation_pi.config import Config
from radiation_pi.emulator import DoseRateEmulator, SensorReadError
from radiation_pi.runner import SimulatorRunner


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def config():
    return Config(
        server_url="http://127.0.0.1:8080", station_id=1,
        detector_ids=(4,), unit="uSv/h",
        measurement_interval_seconds=5,
        heartbeat_interval_seconds=3,
        request_timeout_seconds=1,
        outbox_path=Path("data/outbox.sqlite3"), token="test-token",
    )


class FakeClient:
    def __init__(self):
        self.measurements = []
        self.heartbeats = []

    def send_measurement(self, message):
        self.measurements.append(message)
        raise DeliveryError("simulated outage", retryable=True)

    def send_heartbeat(self, message):
        self.heartbeats.append(message)
        return {"id": len(self.heartbeats)}


class EmulatorTests(unittest.TestCase):
    def test_reproducible_normal_spike_and_failure(self):
        first = DoseRateEmulator(4, seed=7, spike_every=3, failure_every=2)
        second = DoseRateEmulator(4, seed=7, spike_every=3, failure_every=2)
        self.assertEqual(first.read(), second.read())
        with self.assertRaises(SensorReadError):
            first.read()
        with self.assertRaises(SensorReadError):
            second.read()
        self.assertEqual(str(first.read()), "1.200")
        self.assertEqual(str(second.read()), "1.200")

    def test_warning_alarm_recovery_and_failure_order(self):
        sensor = DoseRateEmulator(4, seed=7, warning_every=2,
                                  spike_every=3, failure_every=5)
        self.assertLess(sensor.read(), Decimal("0.5"))
        self.assertEqual(sensor.read(), Decimal("0.600"))
        self.assertEqual(sensor.read(), Decimal("1.200"))
        self.assertEqual(sensor.read(), Decimal("0.600"))
        with self.assertRaises(SensorReadError):
            sensor.read()
        self.assertEqual(sensor.read(), Decimal("1.200"))
        self.assertLess(sensor.read(), Decimal("0.5"))

    def test_heartbeat_continues_after_sensor_and_delivery_failures(self):
        clock = FakeClock()
        client = FakeClient()
        events = []
        SimulatorRunner(
            config(), client=client, emit=events.append,
            failure_every=2, clock=clock.monotonic, sleep=clock.sleep,
        ).run(samples=3)
        self.assertEqual(len(client.measurements), 2)
        self.assertEqual(len(client.heartbeats), 4)
        self.assertEqual([event["event"] for event in events].count("sensor_error"), 1)
        self.assertEqual([event["event"] for event in events].count("delivery_error"), 2)
        self.assertEqual([event["event"] for event in events].count("sent"), 4)
        self.assertEqual(clock.now, 10.0)

    def test_cli_dry_run_needs_no_token_and_does_not_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                'server_url="http://127.0.0.1:8080"\n'
                'station_id=1\ndetector_ids=[4]\nunit="uSv/h"\n'
                'measurement_interval_seconds=5\nheartbeat_interval_seconds=30\n'
                'request_timeout_seconds=1\noutbox_path="data/outbox.sqlite3"\n',
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["--config", str(path), "simulate", "--samples", "1", "--spike-every", "1"])
            self.assertEqual(result, 0)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([event["kind"] for event in events], ["heartbeat", "measurement"])
            self.assertEqual(events[1]["payload"]["value"], 1.2)
            self.assertEqual(events[1]["payload"]["detectorId"], 4)

    def test_custom_sensor_uses_same_runner_and_is_validated(self):
        class FixedSensor:
            detector_id = 4

            def read(self):
                return Decimal("0.125")

        clock = FakeClock()
        events = []
        SimulatorRunner(config(), client=None, emit=events.append,
                        sensors=[FixedSensor()], clock=clock.monotonic,
                        sleep=clock.sleep).run(samples=1)
        measurement = next(event for event in events if event.get("kind") == "measurement")
        self.assertEqual(str(measurement["payload"]["value"]), "0.125")
        with self.assertRaisesRegex(ValueError, "match configured"):
            SimulatorRunner(config(), client=None, emit=events.append,
                            sensors=[DoseRateEmulator(3)])


if __name__ == "__main__":
    unittest.main()
