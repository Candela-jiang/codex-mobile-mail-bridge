from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SENDER = "your-address@gmail.com"
DEFAULT_RECIPIENT = "your-address@gmail.com"
DEFAULT_ALLOWED_SENDER = "your-address@gmail.com"
RUNTIME_FILES = [
    "codex_notify_email.py",
    "codex_inbox_monitor.py",
    "CodexAppRelayPrompt.md",
    "Set-GmailAppPassword.ps1",
    "Start-CodexMobileBridge.ps1",
    "Stop-CodexMobileBridge.ps1",
    "Status-CodexMobileBridge.ps1",
    "Test-CodexMailBridge.ps1",
]


def toml_string(value: str) -> str:
    return json.dumps(str(value))


def parse_notify_from_toml_text(text: str) -> list[str]:
    match = re.search(r"(?ms)^notify\s*=\s*\[(.*?)^\]\s*", text)
    if not match:
        return []
    values = []
    for item in re.finditer(r'"((?:\\.|[^"\\])*)"', match.group(1)):
        try:
            values.append(json.loads(f'"{item.group(1)}"'))
        except Exception:
            values.append(item.group(1))
    return [str(item) for item in values]


def read_existing_notify(config_path: Path) -> list[str]:
    if not config_path.exists():
        return []
    text = config_path.read_text(encoding="utf-8-sig")
    try:
        if tomllib:
            data = tomllib.loads(text)
            notify = data.get("notify", [])
            if isinstance(notify, list):
                return [str(item) for item in notify]
    except Exception:
        pass
    return parse_notify_from_toml_text(text)


def read_runtime_original_notify(runtime_dir: Path) -> list[str]:
    config_path = runtime_dir / "config.json"
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        original = data.get("original_notify", [])
        if isinstance(original, list):
            return [str(item) for item in original]
    except Exception:
        return []
    return []


def same_path(left: str, right: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(str(right)))
    except Exception:
        return False


def notify_points_to_bridge(notify: list[str], notify_script: Path) -> bool:
    return any(same_path(item, notify_script) for item in notify)


def notify_block(python_exe: str, notify_script: Path) -> str:
    return "\n".join(
        [
            "notify = [",
            f"    {toml_string(python_exe)},",
            f"    {toml_string(str(notify_script))},",
            "]",
            "",
        ]
    )


def update_codex_config(config_path: Path, python_exe: str, notify_script: Path) -> Path:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup = config_path.with_suffix(config_path.suffix + f".bak-mobile-mail-bridge-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    text = config_path.read_text(encoding="utf-8-sig") if config_path.exists() else ""
    if config_path.exists():
        shutil.copy2(config_path, backup)
    else:
        backup.write_text("", encoding="utf-8")

    new_notify = notify_block(python_exe, notify_script)
    pattern = re.compile(r"(?ms)^notify\s*=\s*\[.*?^\]\s*")
    if pattern.search(text):
        text = pattern.sub(lambda _match: new_notify, text, count=1)
    else:
        text = new_notify + text
    config_path.write_text(text, encoding="utf-8")
    return backup


def build_config(args: argparse.Namespace, original_notify: list[str]) -> dict:
    recipients = args.recipient or [DEFAULT_RECIPIENT]
    allowed = args.allowed_sender or [DEFAULT_ALLOWED_SENDER]
    return {
        "enabled": False,
        "sender": args.sender,
        "recipients": recipients,
        "smtp_host": args.smtp_host,
        "smtp_port": args.smtp_port,
        "imap_host": args.imap_host,
        "imap_port": args.imap_port,
        "password_env": "CODEX_GMAIL_APP_PASSWORD",
        "dpapi_secret_file": "gmail_app_password.dpapi",
        "subject_prefix": "[Codex]",
        "max_subject_task_chars": 36,
        "self_report_subject_is_task_name": True,
        "inbox_enabled": False,
        "inbox_subject_tag": args.subject_tag,
        "allowed_senders": allowed,
        "poll_seconds": args.poll_seconds,
        "imap_idle_enabled": True,
        "idle_wait_seconds": 300,
        "python_exe": sys.executable,
        "codex_cwd": str(Path(args.codex_cwd).expanduser()),
        "codex_exe": args.codex_exe,
        "codex_sandbox": args.codex_sandbox,
        "codex_timeout_seconds": args.codex_timeout_seconds,
        "command_delivery": args.command_delivery,
        "app_queue_path": "",
        "command_mode": args.command_mode,
        "default_target_session": args.default_target_session,
        "include_git_summary": True,
        "max_git_files": 30,
        "mobile_reply_hint": "直接回复这封邮件即可继续同一个 Codex 任务；新任务请另发邮件并让主题包含 [codex-next]。",
        "max_body_chars": 15000,
        "original_notify": original_notify,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Codex Mobile Mail Bridge runtime files.")
    default_codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    parser.add_argument("--sender", default=DEFAULT_SENDER, help="Gmail address used for SMTP and IMAP.")
    parser.add_argument("--recipient", action="append", help="Email recipient for Codex work reports. Repeatable.")
    parser.add_argument("--allowed-sender", action="append", help="Email address allowed to send [codex-next] commands. Repeatable.")
    parser.add_argument("--codex-home", default=str(default_codex_home), help="Codex home directory.")
    parser.add_argument("--runtime-dir", default="", help="Runtime directory. Defaults to <codex-home>/mobile-mail-bridge.")
    parser.add_argument("--codex-cwd", default=str(Path.cwd()), help="Working directory for email-triggered codex exec commands.")
    parser.add_argument("--codex-exe", default=shutil.which("codex") or "codex", help="Path to codex executable.")
    parser.add_argument("--codex-sandbox", default="read-only", choices=["read-only", "workspace-write", "danger-full-access"])
    parser.add_argument("--codex-timeout-seconds", type=int, default=1800)
    parser.add_argument("--command-delivery", default="exec", choices=["exec", "app_queue"], help="Use exec for background CLI commands or app_queue for visible Codex App delivery.")
    parser.add_argument("--command-mode", default="exec", choices=["exec", "resume"], help="Default inbox route when the subject has no explicit target.")
    parser.add_argument("--default-target-session", default="", help="Session id or thread name used when --command-mode resume is selected.")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--subject-tag", default="[codex-next]")
    parser.add_argument("--smtp-host", default="smtp.gmail.com")
    parser.add_argument("--smtp-port", type=int, default=587)
    parser.add_argument("--imap-host", default="imap.gmail.com")
    parser.add_argument("--imap-port", type=int, default=993)
    parser.add_argument("--skip-config-update", action="store_true", help="Copy files and config without changing Codex config.toml notify.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codex_home = Path(args.codex_home).expanduser()
    runtime_dir = Path(args.runtime_dir).expanduser() if args.runtime_dir else codex_home / "mobile-mail-bridge"
    config_path = codex_home / "config.toml"

    runtime_dir.mkdir(parents=True, exist_ok=True)
    for filename in RUNTIME_FILES:
        shutil.copy2(SCRIPT_DIR / filename, runtime_dir / filename)

    notify_script = runtime_dir / "codex_notify_email.py"
    existing_notify = read_existing_notify(config_path)
    if notify_points_to_bridge(existing_notify, notify_script):
        original_notify = read_runtime_original_notify(runtime_dir)
    else:
        original_notify = existing_notify
    config = build_config(args, original_notify)
    (runtime_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    backup = None
    if not args.skip_config_update:
        backup = update_codex_config(config_path, sys.executable, notify_script)

    print(f"Installed runtime: {runtime_dir}")
    print(f"Runtime config: {runtime_dir / 'config.json'}")
    if backup:
        print(f"Codex config backup: {backup}")
        print(f"Codex notify now points to: {runtime_dir / 'codex_notify_email.py'}")
    print("Next: run Set-GmailAppPassword.ps1, then Start-CodexMobileBridge.ps1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
