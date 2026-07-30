# Codex Mobile Mail Bridge

Codex Mobile Mail Bridge turns a local Codex desktop or CLI workflow into a mobile supervision loop.

It can:

- send Chinese email work reports when a Codex turn completes;
- include model, task name, project path, Git branch, changed files, and diff stat;
- poll a Gmail inbox for whitelisted messages whose subject contains `[codex-next]`;
- run those email instructions through local `codex exec` in a conservative sandbox;
- email the result back so the user can continue guiding a desktop project from a phone.

This is intended for local, user-owned machines. It is not an official OpenAI email interface.

## Install

Run from the plugin folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Install-CodexMobileMailBridge.ps1" `
  -Sender "your-address@gmail.com" `
  -Recipients "your-address@gmail.com","backup@example.com" `
  -AllowedSenders "your-address@gmail.com" `
  -CodexCwd "C:\path\to\your\project"
```

The installer writes runtime files to:

```text
%USERPROFILE%\.codex\mobile-mail-bridge
```

It also backs up and updates `%USERPROFILE%\.codex\config.toml` so Codex calls the mail bridge through `notify`. If a previous `notify` command exists, it is stored as `original_notify` and called by the bridge first.

## Gmail Setup

Create a Gmail App Password, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\mobile-mail-bridge\Set-GmailAppPassword.ps1"
```

The app password is stored with Windows DPAPI for the current Windows user.

## Run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\mobile-mail-bridge\Start-CodexMobileBridge.ps1"
```

Check status:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\mobile-mail-bridge\Status-CodexMobileBridge.ps1"
```

Stop:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\mobile-mail-bridge\Stop-CodexMobileBridge.ps1"
```

## Mobile Commands

Send an email to the configured Gmail inbox from an allowed sender.

- Subject must contain `[codex-next]`.
- Body is the next project instruction.
- The default sandbox is `read-only`.

The reply email includes a work report and project evidence after the command runs.

## Security Notes

- Do not commit `config.json`, `gmail_app_password.dpapi`, logs, or PID files.
- Keep `codex_sandbox` at `read-only` until you explicitly want email-triggered commands to edit files.
- Only whitelist email addresses you control.
- This bridge can send local project details over email. Use it only for projects where that is acceptable.
