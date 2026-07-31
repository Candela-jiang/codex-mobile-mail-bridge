$ErrorActionPreference = "Stop"
$configPath = Join-Path $PSScriptRoot "config.json"
$pidPath = Join-Path $PSScriptRoot "inbox-monitor.pid"
$secretPath = Join-Path $PSScriptRoot "gmail_app_password.dpapi"
$config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json

$monitorPid = ""
$running = $false
if (Test-Path -LiteralPath $pidPath) {
  $monitorPid = (Get-Content -Raw -LiteralPath $pidPath).Trim()
  if ($monitorPid -match '^\d+$') {
    $running = [bool](Get-Process -Id ([int]$monitorPid) -ErrorAction SilentlyContinue)
  }
}

[pscustomobject]@{
  OutboundReports = $config.enabled
  InboxCommands = $config.inbox_enabled
  MonitorRunning = $running
  MonitorPid = $monitorPid
  Sender = $config.sender
  Recipients = ($config.recipients -join ", ")
  AllowedSenders = ($config.allowed_senders -join ", ")
  SubjectTag = $config.inbox_subject_tag
  CommandDelivery = $(if ($config.PSObject.Properties.Name -contains "command_delivery") { $config.command_delivery } else { "exec" })
  CommandMode = $config.command_mode
  DefaultTargetSession = $config.default_target_session
  AppQueuePath = $(if ($config.PSObject.Properties.Name -contains "app_queue_path") { $config.app_queue_path } else { "" })
  PythonExe = $config.python_exe
  CodexCwd = $config.codex_cwd
  Sandbox = $config.codex_sandbox
  GmailSecretSaved = (Test-Path -LiteralPath $secretPath)
} | Format-List
