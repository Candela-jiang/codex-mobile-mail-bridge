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
- Outbound report subjects should be the Codex task name, for example `codex-moblie-mail-bridge`; keep model, project path, and Git evidence in the body.
- The installer backs up `config.toml`, installs the bridge as the new `notify`, and stores any previous `notify` command as `original_notify`.
- On reinstall, the installer must avoid chaining the bridge to itself; it preserves the prior `original_notify` when the current `notify` already points at `codex_notify_email.py`.
- The installer records the setup-time Python executable as `python_exe` so background scripts do not depend on a different machine `PATH`.
- Inbox commands are accepted only from configured `allowed_senders` and only when the subject contains `[codex-next]` or `[codex-next:target]`.
- Plain `[codex-next]` starts a new `codex exec` command in the configured `codex_sandbox`.
- `[codex-next:session-id-or-task-name]` uses `codex exec resume --all <target> -` so the phone command is attached to a specific existing Codex session.
- Runtime config can set `command_mode` to `resume` plus `default_target_session` when the user wants plain `[codex-next]` to continue one chosen session by default.

## Report Contents

The email report should be written in Chinese by default and help a phone-only user decide the next instruction. It includes:

- AI model and provider from Codex config when available.
- project label and working directory.
- task name from the Codex notification or user input fallback.
- report subject from the Codex task name, omitting local paths and long prompts.
- user instruction from the last real user command in the current Codex notification, filtering skill links, image tags, and local paths.
- Git root, branch, changed files, and diff stat when the project is a Git repository.
- final Codex reply from the current notification, without guessing from other sessions by default.
- the route used for inbox command replies, such as new background task or specified session.
- the instruction rule for replying from mobile, including the targeted subject format when enabled.

## Safety

Treat this bridge as a local remote-control surface.

- Keep `codex_sandbox` as `read-only` by default.
- Warn users not to target a session that is already actively running; wait for its report first.
- Never publish `config.json`, `gmail_app_password.dpapi`, logs, PID files, personal email addresses, or machine-specific paths.
- Do not add broad allowed senders.
- Warn the user that project details are sent over email.
- Do not ask for or display Gmail app passwords; the password script stores them with Windows DPAPI.

## Troubleshooting

- If no email is sent, run `Status-CodexMobileBridge.ps1` and check `GmailSecretSaved`.
- If the inbox loop is enabled but idle, inspect `mail-bridge.log`.
- If Codex reports are not firing, restart Codex after install so the updated `notify` setting is loaded.
- If another notification integration stops working, inspect `original_notify` in runtime `config.json`.
