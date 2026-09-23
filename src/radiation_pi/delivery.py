"""Persist before sending and replay pending messages in FIFO order."""

from __future__ import annotations

import time
from collections.abc import Callable

from .client import Client, DeliveryError
from .messages import Heartbeat, Measurement
from .outbox import Outbox


class OutboxSender:
    def __init__(self, client: Client, outbox: Outbox, *, clock: Callable[[], float] = time.time):
        self.client = client
        self.outbox = outbox
        self.clock = clock

    def send_measurement(self, message: Measurement) -> dict:
        return self._submit("measurement", message.message_id,
                            self.client.serialize_measurement(message))

    def send_heartbeat(self, message: Heartbeat) -> dict:
        return self._submit("heartbeat", message.message_id,
                            self.client.serialize_heartbeat(message))

    def _submit(self, kind: str, message_id: str, body: bytes) -> dict:
        item = self.outbox.enqueue(kind, message_id, body)
        events = self.flush()
        for event in events:
            if event["messageId"] == message_id:
                return event
        current = self.outbox.get_by_id(item.id)
        if current is None:
            return {"event": "sent", "messageId": message_id}
        return {"event": current.state, "messageId": message_id,
                "retryAt": current.next_attempt_at if current.state == "pending" else None}

    def flush(self, *, limit: int = 100) -> list[dict]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        events = []
        for _ in range(limit):
            item = self.outbox.pending_head()
            if item is None or item.next_attempt_at > self.clock():
                break
            try:
                response = self.client.send_serialized(item.kind, item.body)
            except DeliveryError as error:
                if error.retryable:
                    retry_at = self.outbox.schedule_retry(
                        item, now=self.clock(), status=error.status
                    )
                    events.append({"event": "retry_scheduled", "messageId": item.message_id,
                                   "kind": item.kind, "status": error.status,
                                   "retryAt": retry_at})
                else:
                    self.outbox.block(item.id, error.status)
                    events.append({"event": "blocked", "messageId": item.message_id,
                                   "kind": item.kind, "status": error.status})
                break
            self.outbox.acknowledge(item.id)
            events.append({"event": "sent", "messageId": item.message_id,
                           "kind": item.kind, "serverId": response.get("id")})
        return events
