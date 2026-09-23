"""Manual commands for validating and sending one message."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

from .client import Client, DeliveryError
from .config import load_config
from .delivery import OutboxSender
from .messages import Heartbeat, Measurement
from .outbox import Outbox
from .runner import SimulatorRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radiation-pi")
    parser.add_argument("--config", default="config.local.toml")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("check-config")
    measurement = actions.add_parser("send-measurement")
    measurement.add_argument("--detector-id", type=int, required=True)
    measurement.add_argument("--value", required=True, help="Decimal dose rate in uSv/h")
    heartbeat = actions.add_parser("send-heartbeat")
    heartbeat.add_argument("--cpu-temp")
    heartbeat.add_argument("--free-disk-gb")
    heartbeat.add_argument("--memory-percent")
    simulator = actions.add_parser("simulate", help="Run local dose-rate emulators")
    simulator.add_argument("--samples", type=int, default=10,
                           help="Measurement cycles per detector (default: 10)")
    simulator.add_argument("--seed", type=int, default=1)
    simulator.add_argument("--warning-every", type=int, default=0,
                           help="Generate 0.600 uSv/h every Nth read; 0 disables")
    simulator.add_argument("--spike-every", type=int, default=0,
                           help="Generate 1.200 uSv/h every Nth read; 0 disables")
    simulator.add_argument("--failure-every", type=int, default=0,
                           help="Fail every Nth sensor read; 0 disables")
    simulator.add_argument("--send", action="store_true",
                           help="Actually POST to the configured server; default is local dry run")
    actions.add_parser("outbox-status", help="Show queued and blocked messages")
    flush = actions.add_parser("outbox-flush", help="Retry due messages in FIFO order")
    flush.add_argument("--limit", type=int, default=100)
    retry = actions.add_parser("outbox-retry", help="Release one blocked message after fixing its cause")
    retry.add_argument("--message-id", required=True)
    args = parser.parse_args(argv)

    try:
        # A dry run never uses a real device token and never opens a connection.
        local_only = args.action in {"outbox-status", "outbox-retry"} or (
            args.action == "simulate" and not args.send
        )
        config = load_config(args.config, token="local-only" if local_only else None)
        if args.action == "check-config":
            print(f"Configuration valid for station {config.station_id}, detectors {config.detector_ids}")
            return 0
        if args.action in {"outbox-status", "outbox-retry", "outbox-flush"}:
            with Outbox(config.outbox_path, station_id=config.station_id,
                        server_url=config.server_url) as outbox:
                if args.action == "outbox-status":
                    print(json.dumps(outbox.summary(), ensure_ascii=False))
                elif args.action == "outbox-retry":
                    if not outbox.retry_blocked(args.message_id):
                        raise ValueError("no blocked message with this message_id")
                    print(f"Message {args.message_id} returned to pending")
                else:
                    events = OutboxSender(Client(config), outbox).flush(limit=args.limit)
                    for event in events:
                        print(json.dumps(event, ensure_ascii=False))
                    remaining = outbox.summary()
                    print(json.dumps({"remaining": len(remaining)}))
                    if any(row["state"] == "blocked" for row in remaining):
                        return 3
                    if remaining:
                        return 2
            return 0
        if args.action == "simulate":
            if (args.samples <= 0 or args.warning_every < 0
                    or args.spike_every < 0 or args.failure_every < 0):
                raise ValueError("samples must be positive; emulator periods must be nonnegative")
            if args.send:
                with Outbox(config.outbox_path, station_id=config.station_id,
                            server_url=config.server_url) as outbox:
                    return _run_simulator(args, config, OutboxSender(Client(config), outbox))
            else:
                return _run_simulator(args, config, None)
        with Outbox(config.outbox_path, station_id=config.station_id,
                    server_url=config.server_url) as outbox:
            sender = OutboxSender(Client(config), outbox)
            if args.action == "send-measurement":
                message = Measurement(args.detector_id, args.value, datetime.now(timezone.utc))
                result = sender.send_measurement(message)
            else:
                message = Heartbeat(
                    cpu_temp=args.cpu_temp,
                    free_disk_gb=args.free_disk_gb,
                    memory_percent=args.memory_percent,
                )
                result = sender.send_heartbeat(message)
        print(json.dumps(result, ensure_ascii=False))
        return {"sent": 0, "pending": 2, "retry_scheduled": 2, "blocked": 3}[result["event"]]
    except (ValueError, OSError, sqlite3.Error, DeliveryError) as error:
        if isinstance(error, DeliveryError):
            print(f"Delivery failed: {error}; retryable={error.retryable}", file=sys.stderr)
            return 2 if error.retryable else 3
        print(f"Configuration or input error: {error}", file=sys.stderr)
        return 1


def _run_simulator(args, config, sender) -> int:
    events = []

    def report(event: dict) -> None:
        events.append(event["event"])
        print(json.dumps(event, ensure_ascii=False, default=float))

    runner = SimulatorRunner(
        config, client=sender,
        emit=report,
        seed=args.seed, warning_every=args.warning_every,
        spike_every=args.spike_every,
        failure_every=args.failure_every,
    )
    runner.run(samples=args.samples)
    if "blocked" in events:
        return 3
    if any(event in {"pending", "retry_scheduled", "delivery_error"} for event in events):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
