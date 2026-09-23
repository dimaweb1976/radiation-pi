$ErrorActionPreference = 'Stop'
$baseUrl = 'http://127.0.0.1:18081'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$adminHeaders = @{ Authorization = 'Bearer pi-integration-admin-token-only' }

try {
    $stations = Invoke-RestMethod -Uri "$baseUrl/api/stations" -TimeoutSec 5
} catch {
    throw 'Test server is unavailable. Start scripts/start-test-server.ps1 and wait for port 18081.'
}
if (@($stations).Count -ne 0) {
    throw 'The test database is not empty. Restart the H2 test server before seeding a new run.'
}

$stationBody = @{
    name = 'Pi Emulator Test Station'
    location = 'Isolated H2 test database'
    ipAddress = '127.0.0.1'
} | ConvertTo-Json
$station = Invoke-RestMethod -Uri "$baseUrl/api/stations" -Method Post `
    -Headers $adminHeaders -ContentType 'application/json' -Body $stationBody -TimeoutSec 5
if ($station.id -ne 1) {
    throw "Expected test station ID 1, got $($station.id). Stop this test server."
}

$detectorBody = @{
    name = 'Pi Emulator Dose Rate'
    location = 'Isolated H2 test database'
    type = 'Gamma Dose Rate'
    stationId = $station.id
} | ConvertTo-Json
$detector = Invoke-RestMethod -Uri "$baseUrl/api/detectors" -Method Post `
    -Headers $adminHeaders -ContentType 'application/json' -Body $detectorBody -TimeoutSec 5
if ($detector.id -ne 1) {
    throw "Expected test detector ID 1, got $($detector.id). Stop this test server."
}

$runDirectory = Join-Path $project ('data/test-runs/' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$configPath = Join-Path $runDirectory 'config.toml'
$config = @"
# Generated for the isolated H2 test server. Do not point this at production.
server_url = "$baseUrl"
station_id = $($station.id)
detector_ids = [$($detector.id)]
unit = "uSv/h"
measurement_interval_seconds = 2
heartbeat_interval_seconds = 5
request_timeout_seconds = 5
outbox_path = "outbox.sqlite3"
"@
# Windows PowerShell 5.1 writes a BOM with -Encoding utf8; tomllib rejects it.
[System.IO.File]::WriteAllText(
    $configPath,
    $config + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)
Write-Host "Test station $($station.id) and detector $($detector.id) created."
Write-Output $configPath
