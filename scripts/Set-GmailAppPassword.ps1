$ErrorActionPreference = "Stop"
$secretPath = Join-Path $PSScriptRoot "gmail_app_password.dpapi"
Write-Host "Paste the Gmail App Password for the configured sender account."
Write-Host "It will be stored with Windows DPAPI for the current Windows user."
$secret = Read-Host "Gmail App Password" -AsSecureString
$secret | ConvertFrom-SecureString | Set-Content -LiteralPath $secretPath -Encoding ASCII
Write-Host "Saved encrypted Gmail app password:"
Write-Host $secretPath
