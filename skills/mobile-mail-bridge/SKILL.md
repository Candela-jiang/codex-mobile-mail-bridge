---
name: mobile-mail-bridge
description: Local email bridge for Codex that sends phone-friendly work reports and accepts safe Gmail follow-up instructions. Use when Codex needs mobile supervision, [codex-next] inbox commands, or stable cross-machine setup and troubleshooting.
---

# Mobile Mail Bridge

## Purpose

Use this skill to set up or operate a local email bridge for Codex work. The bridge is for mobile supervision of desktop work: Codex sends a Chinese computer-side report after each turn, and the user can reply by email with the next project instruction.

## Locate Scripts

The plugin root contains `scripts/`. Resolve script paths relative to the plugin root, not the skill folder:

- `scripts/Install-CodexMobileMailBridge.ps1`
- `scripts/Set-GmailAppPassword.ps1`
- `scripts/Start-CodexMobileBridge.ps1`
- `scripts/Stop-CodexMobileBridge.ps1`
- `scripts/Status-CodexMobileBridge.ps1`
- `scripts/Test-CodexMailBridge.ps1`

After install, runtime files live under `$env:CODEX_HOME\mobile-mail-bridge` when `CODEX_HOME` is set, otherwise `%USERPROFILE%\.codex\mobile-mail-bridge`, unless the installer receives `-RuntimeDir`.

## Install Workflow

1. Ask for the sender Gmail address and at least one report recipient if they were not supplied.
2. Run the installer from the plugin root. Keep the default sandbox `read-only` unless the user explicitly asks to allow email-triggered edits.
3. Tell the user to generate a Gmail App Password, then run `Set-GmailAppPassword.ps1` from the runtime directory.
4. Start the bridge with `Start-CodexMobileBridge.ps1`.
5. Check `Status-CodexMobileBridge.ps1`; confirm `OutboundReports`, `InboxCommands`, and `MonitorRunning` are true, `GmailSecretSaved` is true, and `PythonExe` points to a usable Python runtime.

Example:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Install-CodexMobileMailBridge.ps1" `
  -Sender "user@gmail.com" `
  -Recipients "user@gmail.com","backup@example.com" `
  -AllowedSenders "user@gmail.com" `
  -CodexCwd "C:\path\to\project"
```

## Operation

- Outbound reports are triggered through Codex `notify` on `agent-turn-complete`.
- The installer backs up `config.toml`, installs the bridge as the new `notify`, and stores any previous `notify` command as `original_notify`.
- On reinstall, the installer must avoid chaining the bridge to itself; it preserves the prior `original_notify` when the current `notify` already points at `codex_notify_email.py`.
- The installer records the setup-time Python executable as `python_exe` so background scripts do not depend on a different machine `PATH`.
- Inbox commands are accepted only from configured `allowed_senders` and only when the subject contains `[codex-next]`.
- Email-triggered commands run through `codex exec` in the configured `codex_sandbox`.

## Report Contents

The email report should be written in Chinese by default and help a phone-only user decide the next instruction. It includes:

- AI model and provider from Codex config when available.
- project label and working directory.
- task name from the Codex notification or user input fallback.
- Git root, branch, changed files, and diff stat when the project is a Git repository.
- final Codex reply.
- the instruction rule for replying from mobile.

## Safety

Treat this bridge as a local remote-control surface.

- Keep `codex_sandbox` as `read-only` by default.
- Never publish `config.json`, `gmail_app_password.dpapi`, logs, PID files, personal email addresses, or machine-specific paths.
- Do not add broad allowed senders.
- Warn the user that project details are sent over email.
- Do not ask for or display Gmail app passwords; the password script stores them with Windows DPAPI.

## Troubleshooting

- If no email is sent, run `Status-CodexMobileBridge.ps1` and check `GmailSecretSaved`.
- If the inbox loop is enabled but idle, inspect `mail-bridge.log`.
- If Codex reports are not firing, restart Codex after install so the updated `notify` setting is loaded.
- If another notification integration stops working, inspect `original_notify` in runtime `config.json`.
