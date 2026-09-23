# Local server and Python emulator test run

Use two PowerShell terminals. These commands use the Spring Boot **test classpath** and a fresh in-memory H2 database on port `18081`; they do not use the operational PostgreSQL database or its device tokens. The Java `demo` simulators are not enabled. The Python emulator supplies the measurements and heartbeat.

## Terminal 1: start the isolated server

```powershell
cd D:\RadiationMonitor\radiation-pi
.\scripts\start-test-server.ps1
```

Wait for `Tomcat started on port 18081`. Keep this terminal open.

## Terminal 2: create fixtures and run the emulator

```powershell
cd D:\RadiationMonitor\radiation-pi
$testConfig = .\scripts\seed-test-server.ps1
.\scripts\run-test-emulator.ps1 -ConfigPath $testConfig
```

The seed script requires an empty test database. It creates station 1 and dose-rate detector 1 through the admin API and writes a unique local configuration under the Git-ignored `data/test-runs/` directory. The runner supplies only the test device token. Ten reads take about 20 seconds: two fail deliberately, while the remaining eight include normal (`0.080`–`0.180`), warning (`0.600`) and alarm (`1.200`) values. Heartbeat runs independently. The runner checks that each invocation adds eight measurements, at least three heartbeats, and warning, alarm and normal events. You can rerun the runner with the same configuration without reseeding; it checks only records added during that invocation.

Open [the test dashboard](http://127.0.0.1:18081/) to inspect the records. To inspect the outbox again:

```powershell
$env:PYTHONPATH = 'D:\RadiationMonitor\radiation-pi\src'
python -m radiation_pi --config $testConfig outbox-status
```

An empty array (`[]`) means every generated message was acknowledged. If the emulator reports a delivery error, inspect this outbox before retrying; rerunning the seeding script requires a fresh test server.

When finished, press Ctrl+C in Terminal 1. The H2 database disappears. A later run starts with a fresh server and a new test configuration.
