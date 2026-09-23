"""Load and validate station configuration without reading server secrets."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    server_url: str
    station_id: int
    detector_ids: tuple[int, ...]
    unit: str
    measurement_interval_seconds: float
    heartbeat_interval_seconds: float
    request_timeout_seconds: float
    outbox_path: Path
    token: str

    def __repr__(self) -> str:
        return (
            f"Config(server_url={self.server_url!r}, station_id={self.station_id!r}, "
            f"detector_ids={self.detector_ids!r}, unit={self.unit!r}, "
            "token=<redacted>)"
        )


def load_config(path: str | Path, *, token: str | None = None) -> Config:
    path = Path(path)
    with path.open("rb") as source:
        raw = tomllib.load(source)
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a TOML table")
    allowed = {
        "server_url", "station_id", "detector_ids", "unit",
        "measurement_interval_seconds", "heartbeat_interval_seconds",
        "request_timeout_seconds", "outbox_path",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")

    server_url = _string(raw, "server_url").rstrip("/")
    parsed = urlsplit(server_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("server_url must contain only scheme and host")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("server_url must use HTTPS except for loopback development")
    if not parsed.hostname or not parsed.netloc:
        raise ValueError("server_url must contain a host")

    station_id = _positive_int(raw, "station_id")
    detector_ids = raw.get("detector_ids")
    if (not isinstance(detector_ids, list) or not detector_ids
            or any(type(item) is not int or item <= 0 for item in detector_ids)
            or len(set(detector_ids)) != len(detector_ids)):
        raise ValueError("detector_ids must be a nonempty list of unique positive integers")
    unit = _string(raw, "unit")
    if unit != "uSv/h":
        raise ValueError("the server currently supports only uSv/h")
    supplied_token = token if token is not None else os.environ.get("RADIATION_PI_TOKEN", "")
    if not supplied_token or supplied_token.isspace():
        raise ValueError("RADIATION_PI_TOKEN is required")
    if supplied_token != supplied_token.strip() or "\n" in supplied_token or "\r" in supplied_token:
        raise ValueError("RADIATION_PI_TOKEN has invalid whitespace")

    return Config(
        server_url=server_url,
        station_id=station_id,
        detector_ids=tuple(detector_ids),
        unit=unit,
        measurement_interval_seconds=_positive_number(raw, "measurement_interval_seconds"),
        heartbeat_interval_seconds=_positive_number(raw, "heartbeat_interval_seconds"),
        request_timeout_seconds=_positive_number(raw, "request_timeout_seconds"),
        outbox_path=path.parent / _string(raw, "outbox_path"),
        token=supplied_token,
    )


def _string(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return value


def _positive_int(raw: dict, key: str) -> int:
    value = raw.get(key)
    if type(value) is not int or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _positive_number(raw: dict, key: str) -> float:
    value = raw.get(key)
    if type(value) not in (int, float) or not 0 < value < float("inf"):
        raise ValueError(f"{key} must be a finite positive number")
    return float(value)
