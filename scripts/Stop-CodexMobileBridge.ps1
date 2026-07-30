$ErrorActionPreference = "Stop"
$configPath = Join-Path $PSScriptRoot "config.json"
$pidPath = Join-Path $PSScriptRoot "inbox-monitor.pid"
$logPath = Join-Path $PSScriptRoot "mail-bridge.log"

$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$config.enabled = $false
$config.inbox_enabled = $false
$config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding UTF8

if (Test-Path -LiteralPath $pidPath) {
  $oldPid = Get-Content -Raw -LiteralPath $pidPath
  if ($oldPid -match '^\d+$') {
    $process = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
    if ($process) {
      Stop-Process -Id $process.Id -Force
      Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] mobile bridge monitor stopped: pid=$($process.Id)"
    }
  }
  Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Codex mobile bridge: OFF"
Write-Host "Outbound reports and email-command polling are disabled."
