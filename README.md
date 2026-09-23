# radiation-pi

Python client for one radiation monitoring station. Stage 5 adds a replaceable `Sensor` interface, a server integration suite using an isolated test database, and installation instructions for Raspberry Pi. Physical detector drivers require the actual hardware.

## Identity

One process represents one station. Its station ID, permitted detector IDs and bearer token are configured locally. The server derives station identity from the token; `stationId` is never sent in measurement or heartbeat JSON.

Initial local mappings in the current server database:

| Station | Station ID | Dose-rate detector IDs |
| --- | ---: | --- |
| Riga-Lab-01 | 1 | 4 (DoseRate-01) |
| Liepaja-Post-01 | 2 | 3 (Gamma-01) |

AirTrack-01 (ID 1) and WaterTrack-01 (ID 2) are excluded until their physical quantities, units and thresholds are defined. The current server accepts only `uSv/h`.

## Requirements and setup

- Python 3.11 or newer. The client has no third-party runtime dependencies.
- Use an isolated test server and test database for development. Its station and detector IDs must match the local configuration.

From the project directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item config.example.toml config.local.toml
$env:RADIATION_PI_TOKEN = '<test-device-token-for-this-station>'
.\.venv\Scripts\radiation-pi.exe --config config.local.toml check-config
```

The token comes from the test server's `RADIATION_DEVICE_TOKENS` entry for this station. Do not copy the server's `.env.local.ps1`: it contains administrator, database and other stations' credentials. `config.local.toml` is ignored by Git. An actual Pi must use an HTTPS server URL; plain HTTP is accepted only on loopback for local development.

## Manual sends

These commands write data to the configured server, so check the URL and token first:

```powershell
.\.venv\Scripts\radiation-pi.exe --config config.local.toml send-measurement --detector-id 4 --value 0.546
.\.venv\Scripts\radiation-pi.exe --config config.local.toml send-heartbeat --cpu-temp 42.0 --free-disk-gb 16 --memory-percent 38
```

The measurement timestamp and message ID are generated when the command runs. The CLI stores each message in the SQLite outbox before its first HTTP attempt. A successful HTTP 200 removes it from the outbox. A queued retry exits with code 2, a blocked message with code 3, and invalid configuration or input with code 1. The server's `messageId` deduplication makes replay safe when a response is lost after the server committed the message.

If a manual send is queued, use `outbox-flush` to retry it. Running the original `send-measurement` or `send-heartbeat` command again creates a new observation with a new message ID.

## Local emulator

The emulator reads each configured dose-rate detector every `measurement_interval_seconds` and sends an independent simulated station heartbeat every `heartbeat_interval_seconds`. Its default is a local dry run: it prints JSON events, needs no token, and makes no HTTP requests.

```powershell
.\.venv\Scripts\radiation-pi.exe --config config.example.toml simulate --samples 10 --seed 7
.\.venv\Scripts\radiation-pi.exe --config config.example.toml simulate --samples 10 --seed 7 --warning-every 2 --spike-every 3 --failure-every 5
```

Normal readings vary from 0.080 to 0.180 `uSv/h`. `--warning-every N` produces `0.600 uSv/h`, `--spike-every N` produces `1.200 uSv/h`, and `--failure-every N` simulates a missing sensor reading. On a collision the priority is failure, alarm, then warning. The seed makes values repeatable; message IDs and timestamps remain unique. A failed sensor read does not stop heartbeats. The emulator does not cover air or water detectors.

To write to an **isolated test server**, configure its URL, detector IDs and device token, then add `--send`:

```powershell
$env:RADIATION_PI_TOKEN = '<test-device-token-for-this-station>'
.\.venv\Scripts\radiation-pi.exe --config config.local.toml simulate --samples 10 --seed 7 --send
```

Each attempted send is reported. With `--send`, generated messages use the same outbox as manual sends. A dry run does not create or use an outbox. Use only an isolated test server for emulator writes.

## Outbox and recovery

The default outbox path is `data/outbox.sqlite3` relative to the configuration file. The file is bound to the configured station ID and server URL; a different station or server needs its own outbox path. It contains measurements and health metrics, but no bearer token. Keep it on persistent local storage and include it in device backups. `data/` is ignored by Git.

Pending messages are sent oldest first. Network errors, HTTP 429 and HTTP 5xx keep the same bytes and schedule another attempt after 1, 2, 4, 8, ... seconds, capped at 5 minutes. HTTP 400, 403, 404 and 409 move the message to `blocked` for operator review. A blocked message is retained and is never retried automatically. Later pending messages can proceed on a later flush.

```powershell
.\.venv\Scripts\radiation-pi.exe --config config.local.toml outbox-status
.\.venv\Scripts\radiation-pi.exe --config config.local.toml outbox-flush
```

`outbox-status` lists message IDs, attempts, states and HTTP status without a token. `outbox-flush` needs the device token and sends due messages. While the emulator runs with `--send`, each new event also triggers a flush. When it is stopped, run `outbox-flush` after the retry time or restart the emulator. There is no background daemon yet.

`outbox-flush` and `simulate --send` return code 2 while pending messages remain and code 3 if a blocked message remains. Inspect `outbox-status` before retrying blocked records.

After fixing the cause of a blocked message, explicitly release it and flush:

```powershell
.\.venv\Scripts\radiation-pi.exe --config config.local.toml outbox-retry --message-id <message-id>
.\.venv\Scripts\radiation-pi.exe --config config.local.toml outbox-flush
```

Do not delete the outbox to clear errors: that would discard unsent observations.

## Tests

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

The tests use a local HTTP server or fake clock and never write to PostgreSQL. See [the API contract](docs/API_CONTRACT.md) for request bodies and response handling.

The opt-in [server integration suite](docs/INTEGRATION_TEST.md) starts Spring Boot against an isolated H2 database and checks the full HTTP path. [Pi installation instructions](docs/PI_INSTALL.md) and systemd outbox timer files are prepared for when a device is available. The sensor adapter contract is in `src/radiation_pi/sensor.py`.

For a manual end-to-end run of the server and Python emulators, follow [the local test-run sequence](docs/TEST_RUN.md). It creates disposable station and detector fixtures in H2 and checks measurements, heartbeats and threshold events.
