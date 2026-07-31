from __future__ import annotations

import json
import os
import re
import smtplib
import subprocess
import sys
import traceback
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
CODEX_CONFIG_PATH = Path(os.environ.get("CODEX_CONFIG_PATH", Path.home() / ".codex" / "config.toml"))
SESSION_INDEX_PATH = Path(os.environ.get("CODEX_SESSION_INDEX_PATH", Path.home() / ".codex" / "session_index.jsonl"))
LOG_PATH = ROOT / "mail-bridge.log"


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_codex_config() -> dict:
    if not tomllib:
        return {}
    try:
        with CODEX_CONFIG_PATH.open("rb") as handle:
            return tomllib.load(handle)
    except Exception:
        return {}


def forward_original_notify(config: dict, raw_notification: str) -> None:
    original = config.get("original_notify") or []
    if not original:
        return
    try:
        subprocess.run(
            [*original, raw_notification],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        log(f"original notify failed: {exc}")


def read_dpapi_secret(secret_name: str) -> str:
    if not secret_name:
        return ""
    secret_path = ROOT / secret_name
    if not secret_path.exists():
        return ""

    escaped_path = str(secret_path).replace("'", "''")
    ps = (
        "$ErrorActionPreference='Stop';"
        "Import-Module Microsoft.PowerShell.Security;"
        f"$s=(Get-Content -Raw -LiteralPath '{escaped_path}').Trim();"
        "$sec=ConvertTo-SecureString $s;"
        "$b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec);"
        "try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } "
        "finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }"
    )
    env = os.environ.copy()
    env["PSModulePath"] = env.get("PSModulePath") or (
        r"C:\Program Files\WindowsPowerShell\Modules;"
        r"C:\Windows\system32\WindowsPowerShell\v1.0\Modules"
    )
    result = subprocess.run(
        [
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        log(f"dpapi secret read failed: {result.stderr.strip()}")
        return ""
    return result.stdout.strip()


def get_password(config: dict) -> str:
    env_name = config.get("password_env") or "CODEX_GMAIL_APP_PASSWORD"
    return os.environ.get(env_name, "").strip() or read_dpapi_secret(
        config.get("dpapi_secret_file", "")
    )


def first_non_empty_line(value: str) -> str:
    for line in str(value or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def clean_subject(value: str) -> str:
    value = " ".join((value or "").split())
    if len(value) > 90:
        value = value[:87] + "..."
    return value or "Codex task"


def strip_subject_noise(value: str) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\([A-Za-z]:[\\/].*?\)", "", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s\])>]+", "", text)
    return text.strip(" -_[]()\\/")


def looks_like_instruction_or_path(value: str) -> bool:
    text = str(value or "")
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "c:\\",
            "d:\\",
            ".codex\\skills",
            ".codex/skills",
            "you are codex",
            "邮件正文",
        )
    ) or len(text) > 80


def session_index_title(thread_id: str = "") -> str:
    if not SESSION_INDEX_PATH.exists():
        return ""
    latest = ""
    try:
        for line in SESSION_INDEX_PATH.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except Exception:
                continue
            title = clean_subject(str(item.get("thread_name") or ""))
            if not title:
                continue
            if thread_id and str(item.get("id") or "") == thread_id:
                latest = title
            elif not thread_id:
                latest = title
    except Exception:
        return ""
    return latest


def compact_subject_task(value: str, limit: int) -> str:
    text = strip_subject_noise(value)
    if not text:
        return "工作报告"
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def build_report_subject(notification: dict, config: dict) -> str:
    limit = int(config.get("max_subject_task_chars") or 36)
    return compact_subject_task(resolve_task_name(notification, config), limit)


def resolve_model_label(notification: dict, codex_config: dict) -> str:
    for key in ("model", "model-id", "ai-model"):
        if notification.get(key):
            return str(notification[key])
    provider = codex_config.get("model_provider") or codex_config.get("model_provider_id") or ""
    model = codex_config.get("model") or ""
    if provider and model:
        return f"{model} ({provider})"
    return str(model or provider or "未知")


def resolve_task_name(notification: dict, config=None) -> str:
    for key in ("thread-name", "thread_name", "title", "name"):
        if notification.get(key):
            return clean_subject(str(notification[key]))

    for key in ("thread-id", "thread_id", "session-id", "session_id", "id"):
        if notification.get(key):
            title = session_index_title(str(notification[key]))
            if title:
                return title

    if config and config.get("default_target_session"):
        title = session_index_title(str(config.get("default_target_session")))
        if title:
            return title

    for key in ("task-name", "task_name"):
        if notification.get(key):
            task = clean_subject(str(notification[key]))
            if not looks_like_instruction_or_path(task):
                return task

    latest_title = session_index_title()
    if latest_title:
        return latest_title

    user_messages = notification.get("input-messages") or []
    if user_messages:
        return clean_subject(first_non_empty_line(str(user_messages[0])))
    return clean_subject(notification.get("thread-id", "Codex task"))


def resolve_project_label(cwd: str, codex_config: dict) -> str:
    cwd_text = str(cwd or "").strip()
    projects = codex_config.get("projects") or {}
    if not cwd_text:
        return "未知"

    cwd_norm = cwd_text.casefold().rstrip("\\/")
    best_path = ""
    best_info = {}
    for project_path, project_info in projects.items():
        project_norm = str(project_path).casefold().rstrip("\\/")
        if cwd_norm == project_norm or cwd_norm.startswith(project_norm + "\\") or cwd_norm.startswith(project_norm + "/"):
            if len(project_norm) > len(best_path.casefold()):
                best_path = str(project_path)
                best_info = project_info if isinstance(project_info, dict) else {}

    if best_path:
        trust = best_info.get("trust_level")
        return f"{best_path} ({trust})" if trust else best_path
    return cwd_text


def metadata_lines(notification: dict, config: dict, task_name: str = "") -> list[str]:
    codex_config = load_codex_config()
    cwd = notification.get("cwd") or config.get("codex_cwd") or ""
    return [
        f"模型: {resolve_model_label(notification, codex_config)}",
        f"项目: {short_project_label(resolve_project_label(cwd, codex_config))}",
        f"任务: {task_name or resolve_task_name(notification)}",
    ]


def metadata_block(notification: dict, config: dict, task_name: str = "") -> str:
    return "\n".join(metadata_lines(notification, config, task_name))


def truncate_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...（已截断）"


def compact_text(value: str, limit: int = 600) -> str:
    lines = []
    for line in str(value or "").splitlines():
        stripped = " ".join(line.split())
        if stripped:
            lines.append(stripped)
    return truncate_text("\n".join(lines), limit)


def short_project_label(project_label: str) -> str:
    label = str(project_label or "").strip()
    if not label:
        return "未知"
    trust = ""
    if label.endswith(")") and " (" in label:
        label, trust = label.rsplit(" (", 1)
        trust = f" ({trust}"
    path = Path(label)
    name = path.name or label
    return f"{name}{trust}" if name else label


def user_instruction_summary(messages: list, limit: int = 600) -> str:
    internal_prefixes = (
        "generate 0 to 3 hyperpersonalized suggestions",
        "get an understanding of the user's intent",
    )
    for message in messages:
        text = compact_text(str(message), limit)
        if not text:
            continue
        if any(text.lower().startswith(prefix) for prefix in internal_prefixes):
            continue
        return text
    return "（空）"


def safe_existing_cwd(cwd: str, config: dict) -> str:
    cwd_path = Path(str(cwd or "")).expanduser()
    if cwd_path.exists() and cwd_path.is_dir():
        return str(cwd_path)
    fallback = Path(str(config.get("codex_cwd", ROOT))).expanduser()
    if fallback.exists() and fallback.is_dir():
        return str(fallback)
    return str(ROOT)


def run_text_command(args: list[str], cwd: str, timeout: int = 10) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if err and not text:
            text = err
        return result.returncode, text
    except Exception as exc:
        return 1, str(exc)


def find_git_root(cwd: str) -> str:
    code, text = run_text_command(["git", "rev-parse", "--show-toplevel"], cwd, timeout=5)
    if code == 0 and text:
        return first_non_empty_line(text)
    return ""


def git_status_report(cwd: str, config: dict) -> str:
    if not config.get("include_git_summary", True):
        return ""

    cwd = safe_existing_cwd(cwd, config)
    git_root = find_git_root(cwd)
    if not git_root:
        return ""

    status_code, status_text = run_text_command(["git", "status", "--short"], git_root)
    branch_code, branch_text = run_text_command(["git", "branch", "--show-current"], git_root)
    stat_code, stat_text = run_text_command(["git", "diff", "--stat"], git_root)

    branch = branch_text if branch_code == 0 and branch_text else "（分离 HEAD 或未知）"
    lines = [f"分支: {branch}"]

    if status_code == 0 and status_text:
        status_lines = status_text.splitlines()
        max_files = min(int(config.get("max_git_files") or 30), 12)
        lines.append(f"变更: {len(status_lines)} 个文件")
        lines.extend(status_lines[:max_files])
        if len(status_lines) > max_files:
            lines.append(f"... 还有 {len(status_lines) - max_files} 个文件未列出")
    elif status_code == 0:
        lines.append("变更: 无")
    else:
        lines.append(f"变更: git status 执行失败: {status_text}")

    if stat_code == 0 and stat_text:
        stat_lines = stat_text.splitlines()
        lines.extend(["", "统计:", *stat_lines[:8]])
        if len(stat_lines) > 8:
            lines.append("...（统计已截断）")
    return "\n".join(lines)


def mobile_reply_hint(config: dict) -> str:
    return str(
        config.get("mobile_reply_hint")
        or "用手机回邮件时，请让主题包含 [codex-next]，正文写项目的下一步指令。"
    )


def build_body(notification: dict, config: dict) -> str:
    user_messages = notification.get("input-messages") or []
    assistant_message = notification.get("last-assistant-message") or ""
    task_name = resolve_task_name(notification, config)
    cwd = notification.get("cwd", "")
    evidence = git_status_report(cwd, config)
    body_parts = [
        "Codex 工作报告",
        "",
        f"时间: {datetime.now().strftime('%m-%d %H:%M')}",
        metadata_block(notification, config, task_name),
        "",
        "指令:",
        user_instruction_summary(user_messages, 600),
    ]
    if evidence:
        body_parts.extend(["", "电脑端:", evidence])
    body_parts.extend(
        [
            "",
            "结果:",
            truncate_text(assistant_message.strip() or "（空）", 5000),
            "",
            "下一步:",
            mobile_reply_hint(config),
        ]
    )
    body = "\n".join(body_parts)
    limit = int(config.get("max_body_chars") or 15000)
    if len(body) > limit:
        body = body[:limit] + "\n\n[Truncated by Codex mail bridge]"
    return body


def send_email(notification: dict, config: dict) -> None:
    password = get_password(config)
    if not password:
        log("mail skipped: Gmail app password is not configured")
        return

    sender = config["sender"]
    recipients = config["recipients"]
    subject = build_report_subject(notification, config)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(build_body(notification, config))

    with smtplib.SMTP(config.get("smtp_host", "smtp.gmail.com"), int(config.get("smtp_port", 587)), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(message)
    log(f"mail sent to {', '.join(recipients)}")


def main() -> int:
    raw_notification = os.environ.get("CODEX_MAIL_BRIDGE_TEST_JSON") or (
        sys.argv[1] if len(sys.argv) > 1 else "{}"
    )
    try:
        config = load_config()
    except Exception:
        log("config load failed:\n" + traceback.format_exc())
        return 0

    forward_original_notify(config, raw_notification)

    try:
        notification = json.loads(raw_notification)
    except Exception:
        log("notification json parse failed")
        return 0

    if notification.get("type") != "agent-turn-complete":
        return 0
    if not config.get("enabled", False):
        log("mail skipped: bridge disabled")
        return 0

    try:
        send_email(notification, config)
    except Exception:
        log("mail send failed:\n" + traceback.format_exc())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
