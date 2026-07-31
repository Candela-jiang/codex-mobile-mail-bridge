$ErrorActionPreference = "Stop"
$configPath = Join-Path $PSScriptRoot "config.json"
$pidPath = Join-Path $PSScriptRoot "inbox-monitor.pid"
$logPath = Join-Path $PSScriptRoot "mail-bridge.log"

$config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
$config.enabled = $false
$config.inbox_enabled = $false
$config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding UTF8

if (Test-Path -LiteralPath $pidPath) {
  $oldPid = Get-Content -Raw -LiteralPath $pidPath
  if ($oldPid -match '^\d+$') {
    $process = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
    if ($process) {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
      Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] mobile bridge monitor stopped: pid=$($process.Id)"
    }
  }
  Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

$escapedRoot = [regex]::Escape($PSScriptRoot)
$orphanMonitors = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and
  $_.CommandLine -match "codex_inbox_monitor\.py" -and
  $_.CommandLine -match $escapedRoot
}
foreach ($monitor in $orphanMonitors) {
  $process = Get-Process -Id ([int]$monitor.ProcessId) -ErrorAction SilentlyContinue
  if ($process) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] orphan inbox monitor stopped: pid=$($process.Id)"
  }
}

Write-Host "Codex mobile bridge: OFF"
Write-Host "Outbound reports and email-command polling are disabled."
