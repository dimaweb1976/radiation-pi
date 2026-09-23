# Spring Boot integration test

These tests use a separate **in-memory H2 database** on port `18081`. The server is started from the sibling `radiation-monitoring-system` project with its test classpath. Its normal PostgreSQL URL, credentials and database are overridden for this process. The test-only admin and device tokens cannot authenticate against the normal server unless someone explicitly configures it with those same values.

Open two PowerShell terminals in `radiation-pi`:

```powershell
# Terminal 1: wait for "Tomcat started on port 18081"
.\scripts\start-test-server.ps1
```

```powershell
# Terminal 2: run the opt-in integration suite
.\scripts\run-integration-tests.ps1
```

Stop the test server with Ctrl+C after the suite. H2 data disappears when it stops. Restart it before running the integration suite again, because the suite requires an empty test database and creates station and detector fixtures through the administrator API.

The suite verifies device authorization, detector ownership, warning/alarm/normal transitions, heartbeat deduplication and replay of a measurement after the server committed it but the client lost the response. The queue is SQLite in a temporary directory; no test writes to the operational PostgreSQL database.

The ordinary `python -m unittest discover -s tests -v` command skips these tests unless `RADIATION_INTEGRATION_RUN=1` is set.
