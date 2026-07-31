$ErrorActionPreference = "Stop"
$configPath = Join-Path $PSScriptRoot "config.json"
$pidPath = Join-Path $PSScriptRoot "inbox-monitor.pid"
$logPath = Join-Path $PSScriptRoot "mail-bridge.log"
$monitorPath = Join-Path $PSScriptRoot "codex_inbox_monitor.py"

$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json

function Resolve-PythonCommand {
  param([string]$Configured)
  if (-not [string]::IsNullOrWhiteSpace($Configured)) {
    if (Test-Path -LiteralPath $Configured) { return @($Configured) }
    if (Get-Command $Configured -ErrorAction SilentlyContinue) { return @($Configured) }
  }
  if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
  return @("python")
}
$config.enabled = $true
$config.inbox_enabled = $true
$config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding UTF8

$existing = $null
if (Test-Path -LiteralPath $pidPath) {
  $oldPid = Get-Content -Raw -LiteralPath $pidPath
  if ($oldPid -match '^\d+$') {
    $existing = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
  }
}

if (-not $existing) {
  $pythonCommand = Resolve-PythonCommand -Configured ($(if ($config.PSObject.Properties.Name -contains "python_exe") { $config.python_exe } else { $null }))
  $pythonArgs = @()
  if ($pythonCommand.Count -gt 1) {
    $pythonArgs = $pythonCommand[1..($pythonCommand.Count - 1)]
  }
  $process = Start-Process -FilePath $pythonCommand[0] -ArgumentList @($pythonArgs + @($monitorPath)) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
  $process.Id | Set-Content -LiteralPath $pidPath -Encoding ASCII
  Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] mobile bridge monitor started: pid=$($process.Id)"
  Write-Host "Codex mobile bridge: ON"
  Write-Host "Background inbox monitor pid: $($process.Id)"
} else {
  Write-Host "Codex mobile bridge: ON"
  Write-Host "Inbox monitor already running: pid=$($existing.Id)"
}

Write-Host "Phone workflow:"
Write-Host "1. Codex turn reports will be emailed to the configured recipients."
Write-Host "2. Send Gmail a message with subject containing $($config.inbox_subject_tag) to give the next project instruction."
