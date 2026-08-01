$ErrorActionPreference = "Stop"
$configPath = Join-Path $PSScriptRoot "config.json"
$pidPath = Join-Path $PSScriptRoot "inbox-monitor.pid"
$watchdogPidPath = Join-Path $PSScriptRoot "watchdog.pid"
$logPath = Join-Path $PSScriptRoot "mail-bridge.log"
$startScript = Join-Path $PSScriptRoot "Start-CodexMobileBridge.ps1"

Set-Content -LiteralPath $watchdogPidPath -Value $PID -Encoding ASCII

function Test-RunningPid {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $false }
  $value = (Get-Content -Raw -LiteralPath $Path).Trim()
  if ($value -notmatch '^\d+$') { return $false }
  return [bool](Get-Process -Id ([int]$value) -ErrorAction SilentlyContinue)
}

try {
  while ($true) {
    try {
      $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
    } catch {
      Start-Sleep -Seconds 30
      continue
    }

    $enabled = [bool]$config.enabled -and [bool]$config.inbox_enabled
    if (-not $enabled) {
      Start-Sleep -Seconds 30
      continue
    }

    if (-not (Test-RunningPid -Path $pidPath)) {
      try {
        Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] watchdog restarting mobile bridge"
        & $startScript | Out-Null
      } catch {
        Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] watchdog restart failed: $($_.Exception.Message)"
      }
    }

    Start-Sleep -Seconds 30
  }
} finally {
  Remove-Item -LiteralPath $watchdogPidPath -Force -ErrorAction SilentlyContinue
}
