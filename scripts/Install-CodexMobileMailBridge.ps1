param(
  [Parameter(Mandatory=$true)]
  [string]$Sender,

  [string[]]$Recipients,
  [string[]]$AllowedSenders,
  [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }),
  [string]$RuntimeDir = "",
  [string]$CodexCwd = (Get-Location).Path,
  [string]$CodexExe = "codex",
  [ValidateSet("read-only", "workspace-write", "danger-full-access")]
  [string]$CodexSandbox = "read-only",
  [ValidateSet("exec", "resume")]
  [string]$CommandMode = "exec",
  [string]$DefaultTargetSession = "",
  [int]$PollSeconds = 60,
  [switch]$SkipConfigUpdate
)

$ErrorActionPreference = "Stop"
$installer = Join-Path $PSScriptRoot "install_bridge.py"
$installerArgs = @(
  $installer,
  "--sender", $Sender,
  "--codex-home", $CodexHome,
  "--codex-cwd", $CodexCwd,
  "--codex-exe", $CodexExe,
  "--codex-sandbox", $CodexSandbox,
  "--command-mode", $CommandMode,
  "--poll-seconds", "$PollSeconds"
)

if ($DefaultTargetSession) {
  $installerArgs += @("--default-target-session", $DefaultTargetSession)
}
if ($RuntimeDir) {
  $installerArgs += @("--runtime-dir", $RuntimeDir)
}
foreach ($recipient in ($Recipients | Where-Object { $_ })) {
  $installerArgs += @("--recipient", $recipient)
}
foreach ($allowed in ($AllowedSenders | Where-Object { $_ })) {
  $installerArgs += @("--allowed-sender", $allowed)
}
if ($SkipConfigUpdate) {
  $installerArgs += "--skip-config-update"
}

$pythonCommand = @("python")
try {
  & python --version *> $null
  if ($LASTEXITCODE -ne 0) { throw "python exited with $LASTEXITCODE" }
} catch {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = @("py", "-3")
  } else {
    throw "Python was not found. Install Python 3.8+ or make python/py available on PATH."
  }
}

$pythonArgs = @()
if ($pythonCommand.Count -gt 1) {
  $pythonArgs = $pythonCommand[1..($pythonCommand.Count - 1)]
}

& $pythonCommand[0] @pythonArgs @installerArgs
if ($LASTEXITCODE -ne 0) {
  throw "Installer failed with exit code $LASTEXITCODE"
}
