"""Validated messages matching the current Spring Boot API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4


def _decimal(value: Decimal | str | int, name: str, maximum: Decimal) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be a decimal string, integer or Decimal")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a decimal") from error
    if not result.is_finite() or not 0 <= result <= maximum or result.as_tuple().exponent < -3:
        raise ValueError(f"{name} must be nonnegative, at most {maximum}, with up to 3 decimal places")
    return result


@dataclass(frozen=True)
class Measurement:
    detector_id: int
    value: Decimal
    measured_at: datetime
    message_id: str = field(default_factory=lambda: str(uuid4()))
    unit: str = "uSv/h"

    def __post_init__(self) -> None:
        if type(self.detector_id) is not int or self.detector_id <= 0:
            raise ValueError("detector_id must be a positive integer")
        object.__setattr__(self, "value", _decimal(self.value, "value", Decimal("999999999.999")))
        if self.unit != "uSv/h":
            raise ValueError("unit must be uSv/h")
        if self.measured_at.tzinfo is None or self.measured_at.utcoffset() is None:
            raise ValueError("measured_at must be timezone aware")
        if not self.message_id or len(self.message_id) > 100:
            raise ValueError("message_id must have 1 to 100 characters")

    def payload(self) -> dict:
        return {
            "detectorId": self.detector_id,
            "value": self.value,
            "unit": self.unit,
            "messageId": self.message_id,
            "measuredAt": self.measured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class Heartbeat:
    message_id: str = field(default_factory=lambda: str(uuid4()))
    cpu_temp: Decimal | None = None
    free_disk_gb: Decimal | None = None
    memory_percent: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.message_id or len(self.message_id) > 100:
            raise ValueError("message_id must have 1 to 100 characters")
        for name, maximum in (("cpu_temp", "150"), ("free_disk_gb", "100000"), ("memory_percent", "100")):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _decimal(value, name, Decimal(maximum)))

    def payload(self) -> dict:
        return {
            "messageId": self.message_id,
            "cpuTemp": self.cpu_temp,
            "freeDiskGb": self.free_disk_gb,
            "memoryPercent": self.memory_percent,
        }
