# Device-to-server API contract (Stage 1)

Source of truth: radiation-monitoring-system/src/main/java/com/example/demo/MeasurementRequest.java, HeartbeatRequest.java, MeasurementService.java, HeartbeatService.java, and SecurityConfig.java.

## Connection and identity

- Development base URL: http://127.0.0.1:8080. On a Raspberry Pi use the configured HTTPS server URL and validate its TLS certificate.
- Both writes send Authorization: Bearer <device token> and Content-Type: application/json.
- Each process has exactly one station ID and only its token. The server derives station identity from that token.
- The client never sends the administrator token or database password.
- A configured detector ID must belong to the configured station. The client checks its own mapping before sending; the server also enforces it.

## Measurement

POST /api/measurements

    {
      "detectorId": 4,
      "value": 0.546,
      "unit": "uSv/h",
      "messageId": "a51a0765-0b8a-436d-9108-06d1ad8dc74f",
      "measuredAt": "2026-09-23T12:00:00Z"
    }

- detectorId is a positive ID assigned to this station.
- value is a nonnegative decimal, at most three fractional digits and no more than 999999999.999.
- unit is exactly uSv/h in the current server version.
- measuredAt is the observation time in UTC, encoded as an ISO 8601 instant with Z. The server rejects times more than five minutes in the future.
- messageId is a unique UUID generated once per observation and retained unchanged for every retry.
- The server sets createdAt when it receives the observation. The client cannot set createdAt.

## Heartbeat

POST /api/heartbeat

    {
      "messageId": "ea42e6f0-a3ba-4f44-b122-843513436744",
      "cpuTemp": 42.0,
      "freeDiskGb": 16.0,
      "memoryPercent": 38.0
    }

- messageId is a new UUID for each heartbeat and remains unchanged on retry.
- cpuTemp is optional, in degrees Celsius, from 0 to 150.
- freeDiskGb is optional, in GB, from 0 to 100000.
- memoryPercent is optional, from 0 to 100.
- The server sets lastSeen from receipt time. The client does not supply status or lastSeen.
- Measurement and heartbeat schedules are independent. A sensor read failure must not suppress heartbeat.

## Delivery and responses

- A successful POST returns HTTP 200 with the saved record. Repeating the same messageId and identical data returns the existing record without another event.
- A timeout, connection failure, HTTP 429 or HTTP 5xx leaves the exact serialized message in the local SQLite outbox for later retry. Never generate a new messageId for a retry.
- HTTP 400 means invalid payload; HTTP 403 means missing token, wrong role or detector assigned to another station; HTTP 404 means unknown station or detector; HTTP 409 means the messageId was reused with different data. These messages are retained as blocked and require operator attention rather than blind retries.
- The outbox removes a message only after an HTTP 200 response containing a saved record. Pending messages are retried oldest first with capped exponential delay. A blocked message can be released explicitly after its cause is fixed.

## Test isolation

Emulators will target a separate test server/database and test device tokens by default. They must not write to the existing radiation database or reuse operational station IDs unless explicitly configured for an integration test.

## Current scope

The first emulator will produce dose-rate observations for detector 4 at station 1 or detector 3 at station 2. Air and water monitoring require a separate unit and threshold decision on the server before client support.
