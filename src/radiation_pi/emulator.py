"""Reproducible local dose-rate and station-health emulators."""

from __future__ import annotations

from decimal import Decimal
from random import Random

from .messages import Heartbeat


class SensorReadError(Exception):
    """A simulated sensor did not return a reading."""


class DoseRateEmulator:
    def __init__(self, detector_id: int, *, seed: int = 1, warning_every: int = 0,
                 spike_every: int = 0, failure_every: int = 0):
        if type(detector_id) is not int or detector_id <= 0:
            raise ValueError("detector_id must be positive")
        if type(warning_every) is not int or warning_every < 0:
            raise ValueError("warning_every must be a nonnegative integer")
        if type(spike_every) is not int or spike_every < 0:
            raise ValueError("spike_every must be a nonnegative integer")
        if type(failure_every) is not int or failure_every < 0:
            raise ValueError("failure_every must be a nonnegative integer")
        self.detector_id = detector_id
        self.warning_every = warning_every
        self.spike_every = spike_every
        self.failure_every = failure_every
        self._random = Random(seed)
        self._count = 0

    def read(self) -> Decimal:
        self._count += 1
        if self.failure_every and self._count % self.failure_every == 0:
            raise SensorReadError(f"detector {self.detector_id}: simulated read failure")
        if self.spike_every and self._count % self.spike_every == 0:
            return Decimal("1.200")
        if self.warning_every and self._count % self.warning_every == 0:
            return Decimal("0.600")
        # Typical background is around 0.12 uSv/h. Decimal(str(...)) avoids
        # binary float artifacts before rounding to the server's precision.
        value = Decimal(str(self._random.uniform(0.080, 0.180)))
        return value.quantize(Decimal("0.001"))


class HealthEmulator:
    def __init__(self, *, seed: int = 1):
        self._random = Random(seed)

    def read(self) -> Heartbeat:
        cpu = Decimal(str(self._random.uniform(38, 49))).quantize(Decimal("0.1"))
        disk = Decimal(str(self._random.uniform(14, 18))).quantize(Decimal("0.1"))
        memory = Decimal(str(self._random.uniform(30, 50))).quantize(Decimal("0.1"))
        return Heartbeat(cpu_temp=cpu, free_disk_gb=disk, memory_percent=memory)
