$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$runtimeDir = Join-Path (Split-Path $projectDir -Parent) 'snowluma相关/runtime-v1.14.22'
$runtimeFile = Join-Path $projectDir 'runtime.json'
$pythonCandidates = @()
if (Test-Path -LiteralPath $runtimeFile) {
    $runtimeSettings = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $pythonCandidates += $runtimeSettings.python_executable
}
$pythonCandidates += @(Get-Command python -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
$pythonPath = $null
foreach ($candidate in ($pythonCandidates | Select-Object -Unique)) {
    if (-not $candidate -or -not (Test-Path -LiteralPath $candidate)) { continue }
    & $candidate -c 'import aiohttp, cryptography, zstandard; from zoneinfo import ZoneInfo; ZoneInfo(''Asia/Shanghai'')' 2>$null
    if ($LASTEXITCODE -eq 0) { $pythonPath = $candidate; break }
}
if (-not $pythonPath) { throw 'No Python runtime with reminder dependencies was found. Check runtime.json.' }
$startMutex = New-Object System.Threading.Mutex($false, 'Local\PersonalMessageReminderStart')
if (-not $startMutex.WaitOne(0)) { Write-Output 'Reminder startup is already in progress.'; exit 0 }
try {
if (-not (Test-Path (Join-Path $projectDir 'secrets.dpapi'))) { throw 'Run bootstrap.py first.' }
if (-not (Get-NetTCPConnection -LocalPort 5100 -State Listen -ErrorAction SilentlyContinue)) {
    $env:SNOWLUMA_WEBUI_HOST = '127.0.0.1'
    $env:SNOWLUMA_WEBUI_PORT = '5100'
    $personalConfig = Get-Content -LiteralPath (Join-Path $projectDir 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $env:SNOWLUMA_PERSONAL_ACCOUNT = [string]$personalConfig.qq_account
    $env:SNOWLUMA_HOOK_AUTOLOAD = 'false'
    Start-Process -FilePath (Join-Path $runtimeDir 'node.exe') -ArgumentList 'index.mjs' -WorkingDirectory $runtimeDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'startup.out.log') -RedirectStandardError (Join-Path $runtimeDir 'startup.err.log')
}
if (-not (Get-NetTCPConnection -LocalPort 31996 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $pythonPath -ArgumentList 'relay.py' -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectDir 'relay.out.log') -RedirectStandardError (Join-Path $projectDir 'relay.err.log')
}
$reminderConfig = Get-Content -LiteralPath (Join-Path $projectDir 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($reminderConfig.wechat_enabled -and -not (Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'wechat_listener\.py' })) {
    Start-Process -FilePath $pythonPath -ArgumentList 'wechat_listener.py' -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectDir 'wechat.out.log') -RedirectStandardError (Join-Path $projectDir 'wechat.err.log')
}
for ($attempt = 0; $attempt -lt 8; $attempt++) {
    try {
        $status = Invoke-RestMethod 'http://127.0.0.1:31996/status' -TimeoutSec 1
        break
    } catch { Start-Sleep -Milliseconds 250 }
}
if (-not $status) { throw 'Reminder failed to become ready. Check relay.err.log.' }
Write-Output 'SnowLuma: http://127.0.0.1:5100/ ; reminder status: http://127.0.0.1:31996/status'
} finally {
    $startMutex.ReleaseMutex()
    $startMutex.Dispose()
}
