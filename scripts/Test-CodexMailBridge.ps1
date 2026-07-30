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
$old = $env:CODEX_MAIL_BRIDGE_TEST_JSON
try {
  $env:CODEX_MAIL_BRIDGE_TEST_JSON = $notification
  & python $script
} finally {
  $env:CODEX_MAIL_BRIDGE_TEST_JSON = $old
}
if ($LASTEXITCODE -ne 0) {
  throw "Mail bridge test exited with code $LASTEXITCODE"
}
Write-Host "Test finished. Check mail-bridge.log for send status."
