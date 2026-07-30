param(
  [Parameter(Mandatory=$true)]
  [string]$Sender,

  [string[]]$Recipients,
  [string[]]$AllowedSenders,
  [string]$CodexHome = "$env:USERPROFILE\.codex",
  [string]$RuntimeDir = "",
  [string]$CodexCwd = (Get-Location).Path,
  [string]$CodexExe = "codex",
  [ValidateSet("read-only", "workspace-write", "danger-full-access")]
  [string]$CodexSandbox = "read-only",
  [int]$PollSeconds = 60,
  [switch]$SkipConfigUpdate
)

$ErrorActionPreference = "Stop"
$installer = Join-Path $PSScriptRoot "install_bridge.py"
$args = @(
  $installer,
  "--sender", $Sender,
  "--codex-home", $CodexHome,
  "--codex-cwd", $CodexCwd,
  "--codex-exe", $CodexExe,
  "--codex-sandbox", $CodexSandbox,
  "--poll-seconds", "$PollSeconds"
)

if ($RuntimeDir) {
  $args += @("--runtime-dir", $RuntimeDir)
}
foreach ($recipient in ($Recipients | Where-Object { $_ })) {
  $args += @("--recipient", $recipient)
}
foreach ($allowed in ($AllowedSenders | Where-Object { $_ })) {
  $args += @("--allowed-sender", $allowed)
}
if ($SkipConfigUpdate) {
  $args += "--skip-config-update"
}

& python @args
if ($LASTEXITCODE -ne 0) {
  throw "Installer failed with exit code $LASTEXITCODE"
}
