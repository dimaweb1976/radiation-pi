$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$oldPythonPath = $env:PYTHONPATH
$oldIntegrationFlag = $env:RADIATION_INTEGRATION_RUN

try {
    $env:PYTHONPATH = Join-Path $project 'src'
    $env:RADIATION_INTEGRATION_RUN = '1'
    Push-Location $project
    try {
        python -m unittest discover -s tests -p test_server_integration.py -v
        if ($LASTEXITCODE -ne 0) { throw "Integration tests failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:RADIATION_INTEGRATION_RUN = $oldIntegrationFlag
}
