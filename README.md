# Codex Mobile Mail Bridge

A local email bridge for Codex. It turns desktop work into phone-ready reports
and lets you send back safe follow-up instructions from Gmail.

It can:

- send Chinese email work reports when a Codex turn completes;
- include model, task name, project path, Git branch, changed files, and diff stat;
- keep report email subjects compact, with full project metadata in the body;
- poll a Gmail inbox for whitelisted messages whose subject contains `[codex-next]`;
- run those email instructions through local `codex exec` in a conservative sandbox;
- resume a specific existing Codex session when the subject uses `[codex-next:session-id-or-task-name]`;
- email the result back so the user can continue guiding a desktop project from a phone.

This is intended for local, user-owned machines. It is not an official OpenAI email interface.

## Install

Requires Python 3.8+ and PowerShell on Windows.

Run from the plugin folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Install-CodexMobileMailBridge.ps1" `
  -Sender "your-address@gmail.com" `
  -Recipients "your-phone-inbox@example.com" `
  -AllowedSenders "your-phone-inbox@example.com" `
  -CodexCwd "C:\path\to\your\project"
```

Keep real email addresses in your local runtime `config.json` or installer
arguments, not in committed files.

The installer writes runtime files to:

```text
%CODEX_HOME%\mobile-mail-bridge
```

If `CODEX_HOME` is not set, this is `%USERPROFILE%\.codex\mobile-mail-bridge`.
It also backs up and updates the matching Codex `config.toml` so Codex calls the mail bridge through `notify`. If a previous `notify` command exists, it is stored as `original_notify` and called by the bridge first.
Re-running the installer detects an existing mail-bridge `notify` entry and preserves the earlier `original_notify` instead of chaining the bridge to itself.

The installer records the Python executable used during setup in runtime
`config.json` so the background monitor can start reliably even when `python`
is not on `PATH`.

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

To target an existing Codex conversation, put the target after a colon:

```text
[codex-next:019fb407-95d7-7940-b687-b85ea0226ebe]
```

You can also use a task name when it is unique enough:

```text
[codex-next:codex-moblie-mail-bridge]
```

Plain `[codex-next]` starts a new background `codex exec` task. Targeted
subjects use `codex exec resume --all <target> -`, so the command is attached
to the chosen Codex session.

The reply email includes a work report and project evidence after the command runs.

## Report Subjects

Outgoing Codex work reports use the Codex task name as the subject, such as:

```text
codex-moblie-mail-bridge
```

Long prompts and local paths are kept out of the subject. The full model,
project, task, and Git details remain in the email body. Adjust
`max_subject_task_chars` in runtime `config.json` (default 36) if you want a longer or
shorter task label.

The report body keeps `指令` short by showing the user's latest actual command.
It filters internal skill links, image tags, and local file paths. `结果` uses
the final Codex output for that turn.

## Security Notes

- Do not commit `config.json`, `gmail_app_password.dpapi`, logs, or PID files.
- Do not commit `processed_message_ids.json`; it is only a local duplicate-check cache.
- Prefer `CODEX_HOME` and environment-variable paths over machine-specific absolute paths when sharing setup notes across computers.
- Keep `codex_sandbox` at `read-only` until you explicitly want email-triggered commands to edit files.
- Targeted resume commands continue the selected Codex session's context. Avoid sending a new email to a session that is already actively running.
- Only whitelist email addresses you control.
- This bridge can send local project details over email. Use it only for projects where that is acceptable.
