"""Contract for a dose-rate source, whether emulated or physical."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol


class Sensor(Protocol):
    detector_id: int

    def read(self) -> Decimal:
        """Return a dose rate in uSv/h or raise SensorReadError."""
