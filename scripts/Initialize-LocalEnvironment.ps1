$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    Write-Output 'Existing local environment preserved.'
    exit 0
}
$randomBytes = New-Object byte[] 24
$randomSource = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $randomSource.GetBytes($randomBytes) } finally { $randomSource.Dispose() }
$localPassword = [System.BitConverter]::ToString($randomBytes).Replace('-', '').ToLowerInvariant()
$lines = @('POSTGRES_USER=rdp_local', ('POSTGRES_PASSWORD=' + $localPassword), 'POSTGRES_DB=robotics_data', 'API_PORT=8010')
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envPath, $lines, $utf8)
Write-Output 'Created local .env with a random database password; contents were not displayed.'
