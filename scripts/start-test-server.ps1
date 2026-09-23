$ErrorActionPreference = 'Stop'

$serverProject = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\radiation-monitoring-system')).Path
$old = @{}
$settings = @{
    SPRING_DATASOURCE_URL = 'jdbc:h2:mem:piintegration;MODE=PostgreSQL;NON_KEYWORDS=VALUE;DB_CLOSE_DELAY=-1'
    SPRING_DATASOURCE_DRIVER_CLASS_NAME = 'org.h2.Driver'
    SPRING_DATASOURCE_USERNAME = 'sa'
    SPRING_DATASOURCE_PASSWORD = ''
    SPRING_JPA_HIBERNATE_DDL_AUTO = 'create-drop'
    SERVER_ADDRESS = '127.0.0.1'
    SERVER_PORT = '18081'
    RADIATION_ADMIN_TOKEN = 'pi-integration-admin-token-only'
    RADIATION_DEVICE_TOKENS = '1:pi-integration-device-token-only'
}

foreach ($name in $settings.Keys) {
    $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
}

try {
    Push-Location $serverProject
    try {
        & (Join-Path $serverProject 'mvnw.cmd') 'spring-boot:test-run'
    } finally {
        Pop-Location
    }
} finally {
    foreach ($name in $settings.Keys) {
        [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process')
    }
}
