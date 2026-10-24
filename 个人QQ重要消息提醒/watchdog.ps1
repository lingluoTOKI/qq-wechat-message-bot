# BOT watchdog: 持续检查 relay.py 和 wechat_listener.py，挂了自动重启
$ErrorActionPreference = 'Continue'
$projectDir = Split-Path $MyInvocation.MyCommand.Path -Parent
$runtimeFile = Join-Path $projectDir 'runtime.json'

# 解析 Python 路径
$pythonCandidates = @()
if (Test-Path -LiteralPath $runtimeFile) {
    $runtimeSettings = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $pythonCandidates += $runtimeSettings.python_executable
}
$pythonCandidates += @(Get-Command python -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
$pythonPath = $null
foreach ($candidate in ($pythonCandidates | Select-Object -Unique)) {
    if (-not $candidate -or -not (Test-Path -LiteralPath $candidate)) { continue }
    $pythonPath = $candidate; break
}
if (-not $pythonPath) { Write-Error 'No Python found'; exit 1 }

function Test-RelayAlive {
    try {
        $r = Invoke-RestMethod 'http://127.0.0.1:31996/status' -TimeoutSec 2
        return $true
    } catch { return $false }
}

function Start-Relay {
    $outLog = Join-Path $projectDir 'relay.out.log'
    $errLog = Join-Path $projectDir 'relay.err.log'
    Start-Process -FilePath $pythonPath -ArgumentList 'relay.py' -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
}

function Test-WechatAlive {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'wechat_listener\.py'
    }
}

function Start-Wechat {
    $outLog = Join-Path $projectDir 'wechat.out.log'
    $errLog = Join-Path $projectDir 'wechat.err.log'
    Start-Process -FilePath $pythonPath -ArgumentList 'wechat_listener.py' -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
}

# 主循环：每 15 秒检查一次
while ($true) {
    if (-not (Test-RelayAlive)) {
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -LiteralPath (Join-Path $projectDir 'watchdog.log') "[$ts] relay not responding, restarting..."
        Start-Relay
        Start-Sleep -Seconds 10
    }

    $cfg = Get-Content -LiteralPath (Join-Path $projectDir 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.wechat_enabled -and -not (Test-WechatAlive)) {
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -LiteralPath (Join-Path $projectDir 'watchdog.log') "[$ts] wechat_listener not running, restarting..."
        Start-Wechat
    }

    Start-Sleep -Seconds 15
}
