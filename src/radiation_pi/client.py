"""Single-attempt HTTP sender; serialized outbox payloads stay unchanged."""

from __future__ import annotations

import json
import ssl
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config
from .messages import Heartbeat, Measurement


class DeliveryError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Client:
    def __init__(self, config: Config):
        self.config = config

    def send_measurement(self, measurement: Measurement) -> dict:
        return self.send_serialized("measurement", self.serialize_measurement(measurement))

    def serialize_measurement(self, measurement: Measurement) -> bytes:
        if measurement.detector_id not in self.config.detector_ids:
            raise ValueError("detector_id is not configured for this station")
        if measurement.unit != self.config.unit:
            raise ValueError("measurement unit does not match configuration")
        return _serialize(measurement.payload())

    def send_heartbeat(self, heartbeat: Heartbeat) -> dict:
        return self.send_serialized("heartbeat", self.serialize_heartbeat(heartbeat))

    def serialize_heartbeat(self, heartbeat: Heartbeat) -> bytes:
        return _serialize(heartbeat.payload())

    def send_serialized(self, kind: str, body: bytes) -> dict:
        paths = {"measurement": "/api/measurements", "heartbeat": "/api/heartbeat"}
        if kind not in paths:
            raise ValueError("unknown message kind")
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        request = Request(
            self.config.server_url + paths[kind],
            data=body,
            headers={
                "Authorization": "Bearer " + self.config.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.request_timeout_seconds, context=ssl.create_default_context()) as response:
                if response.status != 200:
                    raise DeliveryError(f"unexpected HTTP {response.status}", retryable=response.status >= 500, status=response.status)
                result = json.load(response)
                if not isinstance(result, dict) or "id" not in result:
                    raise DeliveryError("invalid success response", retryable=True)
                return result
        except HTTPError as error:
            # Never include response bodies or request headers: they may contain secrets.
            error.close()
            raise DeliveryError(f"HTTP {error.code}", retryable=error.code >= 500 or error.code == 429, status=error.code) from error
        except (URLError, TimeoutError, OSError) as error:
            raise DeliveryError("network or TLS error", retryable=True) from error
        except json.JSONDecodeError as error:
            raise DeliveryError("invalid success response", retryable=True) from error


def _serialize(payload: dict) -> bytes:
    return json.dumps(payload, default=_encode_decimal, separators=(",", ":")).encode("utf-8")


def _encode_decimal(value: object) -> float:
    if isinstance(value, Decimal):
        # API values have at most three decimals and are below 1e9; their
        # decimal spelling survives Python's shortest float representation.
        return float(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")
