"""Independent measurement and heartbeat schedules for the emulator."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone

from .client import Client, DeliveryError
from .config import Config
from .emulator import DoseRateEmulator, HealthEmulator, SensorReadError
from .messages import Measurement
from .sensor import Sensor


class SimulatorRunner:
    def __init__(self, config: Config, *, client: Client | None,
                 emit: Callable[[dict], None], seed: int = 1,
                 warning_every: int = 0, spike_every: int = 0,
                 failure_every: int = 0,
                 sensors: list[Sensor] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self.client = client
        self.emit = emit
        self.clock = clock
        self.sleep = sleep
        self.sensors = sensors if sensors is not None else [
            DoseRateEmulator(detector_id, seed=seed + detector_id,
                             warning_every=warning_every,
                             spike_every=spike_every, failure_every=failure_every)
            for detector_id in config.detector_ids
        ]
        configured = set(config.detector_ids)
        actual = [sensor.detector_id for sensor in self.sensors]
        if not actual or len(actual) != len(set(actual)) or set(actual) != configured:
            raise ValueError("sensors must match configured detector_ids exactly")
        self.health = HealthEmulator(seed=seed)

    def run(self, *, samples: int) -> None:
        if type(samples) is not int or samples <= 0:
            raise ValueError("samples must be a positive integer")
        next_measurement = self.clock()
        next_heartbeat = next_measurement
        measurement_ticks = 0

        while measurement_ticks < samples:
            now = self.clock()
            if now >= next_heartbeat:
                self._heartbeat()
                next_heartbeat = max(next_heartbeat + self.config.heartbeat_interval_seconds,
                                     self.clock() + self.config.heartbeat_interval_seconds)
            if now >= next_measurement:
                for sensor in self.sensors:
                    self._measurement(sensor)
                measurement_ticks += 1
                next_measurement = max(next_measurement + self.config.measurement_interval_seconds,
                                       self.clock() + self.config.measurement_interval_seconds)
            if measurement_ticks < samples:
                self.sleep(max(0.0, min(next_measurement, next_heartbeat) - self.clock()))

    def _measurement(self, sensor: Sensor) -> None:
        try:
            value = sensor.read()
        except SensorReadError as error:
            self.emit({"event": "sensor_error", "detectorId": sensor.detector_id,
                       "message": str(error)})
            return
        message = Measurement(sensor.detector_id, value, datetime.now(timezone.utc))
        self._deliver("measurement", message.payload(),
                      lambda: self.client.send_measurement(message))

    def _heartbeat(self) -> None:
        message = self.health.read()
        self._deliver("heartbeat", message.payload(),
                      lambda: self.client.send_heartbeat(message))

    def _deliver(self, kind: str, payload: dict, send: Callable[[], dict]) -> None:
        if self.client is None:
            self.emit({"event": "dry_run", "kind": kind, "payload": payload})
            return
        try:
            result = send()
            if "event" in result:
                self.emit({"kind": kind, **result})
            else:
                self.emit({"event": "sent", "kind": kind,
                           "messageId": payload["messageId"], "serverId": result.get("id")})
        except DeliveryError as error:
            self.emit({"event": "delivery_error", "kind": kind,
                       "messageId": payload["messageId"],
                       "retryable": error.retryable, "status": error.status})
