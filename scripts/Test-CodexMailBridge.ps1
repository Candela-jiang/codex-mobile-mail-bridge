$ErrorActionPreference = "Stop"
$notification = @{
  type = "agent-turn-complete"
  "thread-id" = "test-thread"
  "turn-id" = "test-turn"
  cwd = (Get-Location).Path
  "input-messages" = @("Mail bridge test")
  "last-assistant-message" = "This is a test email from the Codex Mobile Mail Bridge."
} | ConvertTo-Json -Compress

$script = Join-Path $PSScriptRoot "codex_notify_email.py"
$configPath = Join-Path $PSScriptRoot "config.json"
$pythonCommand = @("python")
if (Test-Path -LiteralPath $configPath) {
  $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
  if ($config.PSObject.Properties.Name -contains "python_exe" -and -not [string]::IsNullOrWhiteSpace($config.python_exe)) {
    if ((Test-Path -LiteralPath $config.python_exe) -or (Get-Command $config.python_exe -ErrorAction SilentlyContinue)) {
      $pythonCommand = @($config.python_exe)
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
      $pythonCommand = @("py", "-3")
    }
  }
}
$old = $env:CODEX_MAIL_BRIDGE_TEST_JSON
try {
  $env:CODEX_MAIL_BRIDGE_TEST_JSON = $notification
  $pythonArgs = @()
  if ($pythonCommand.Count -gt 1) {
    $pythonArgs = $pythonCommand[1..($pythonCommand.Count - 1)]
  }
  & $pythonCommand[0] @($pythonArgs + @($script))
} finally {
  $env:CODEX_MAIL_BRIDGE_TEST_JSON = $old
}
if ($LASTEXITCODE -ne 0) {
  throw "Mail bridge test exited with code $LASTEXITCODE"
}
Write-Host "Test finished. Check mail-bridge.log for send status."
