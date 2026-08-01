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
from email.utils import make_msgid
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
SESSIONS_ROOT = Path(os.environ.get("CODEX_SESSIONS_ROOT", Path.home() / ".codex" / "sessions"))
LOG_PATH = ROOT / "mail-bridge.log"
MAIL_ROUTES_PATH = ROOT / "mail_thread_routes.json"
MAX_MAIL_ROUTES = 500


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


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding="utf-8")


def resolve_original_notify_command(config: dict) -> list[str]:
    original = list(config.get("original_notify") or [])
    if original and Path(str(original[0])).exists():
        return original

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        runtimes_root = Path(local_app_data) / "OpenAI" / "Codex" / "runtimes"
        try:
            candidates = sorted(
                runtimes_root.rglob("codex-computer-use.exe"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            candidates = []
        if candidates:
            resolved = [str(candidates[0]), *(original[1:] if len(original) > 1 else ["turn-ended"])]
            if resolved != original:
                config["original_notify"] = resolved
                try:
                    save_config(config)
                except Exception:
                    pass
            return resolved
    return original


def forward_original_notify(config: dict, raw_notification: str) -> None:
    original = resolve_original_notify_command(config)
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


def normalize_email_message_id(value: str) -> str:
    return str(value or "").strip().strip("<>").lower()


def strip_reply_prefixes_subject(subject: str) -> str:
    value = str(subject or "").strip()
    while True:
        lowered = value.lower()
        reply_prefixes = (
            "re:",
            "fw:",
            "fwd:",
            "回复:",
            "回复：",
            "答复:",
            "答复：",
            "转发:",
            "转发：",
        )
        matched_prefix = next((prefix for prefix in reply_prefixes if lowered.startswith(prefix)), "")
        if matched_prefix:
            value = value[len(matched_prefix) :].strip()
            continue
        return value


def subject_key(subject: str) -> str:
    return strip_reply_prefixes_subject(clean_subject(subject)).casefold()


def load_mail_routes() -> dict:
    if not MAIL_ROUTES_PATH.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(MAIL_ROUTES_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"version": 1, "items": []}
    if not isinstance(data, dict):
        return {"version": 1, "items": []}
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    return {"version": 1, "items": [item for item in items if isinstance(item, dict)]}


def save_mail_routes(data: dict) -> None:
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    data["items"] = items[-MAX_MAIL_ROUTES:]
    MAIL_ROUTES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_mail_route(message_id: str, notification: dict, config: dict, subject: str, task_name: str) -> None:
    normalized_id = normalize_email_message_id(message_id)
    if not normalized_id:
        return
    thread_id = notification_thread_id(notification, task_name)
    if not thread_id:
        return

    data = load_mail_routes()
    items = [
        item
        for item in data.get("items", [])
        if normalize_email_message_id(str(item.get("message_id") or "")) != normalized_id
    ]
    items.append(
        {
            "message_id": normalized_id,
            "thread_id": thread_id,
            "task_name": task_name,
            "subject": subject,
            "subject_key": subject_key(subject),
            "cwd": str(notification.get("cwd") or config.get("codex_cwd") or ""),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    data["items"] = items
    save_mail_routes(data)
    log(f"mail route saved: message_id={normalized_id}; thread_id={thread_id}; subject={subject}")


def lookup_mail_route_by_message_ids(message_ids: list[str]) -> dict | None:
    ids = {normalize_email_message_id(item) for item in message_ids if normalize_email_message_id(item)}
    if not ids:
        return None
    for item in reversed(load_mail_routes().get("items", [])):
        if normalize_email_message_id(str(item.get("message_id") or "")) in ids:
            return item
    return None


def lookup_mail_route_by_subject(subject: str) -> dict | None:
    key = subject_key(subject)
    if not key:
        return None
    matches = [
        item
        for item in load_mail_routes().get("items", [])
        if str(item.get("subject_key") or "") == key
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return matches[-1]
    return None


def strip_subject_noise(value: str) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\([A-Za-z]:[\\/].*?\)", "", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s\])>]+", "", text)
    return text.strip(" -_[]()\\/")


def looks_like_instruction_or_path(value: str) -> bool:
    text = str(value or "")
    lowered = text.lower()
    if re.fullmatch(r"\$[\w-]+", text.strip()):
        return True
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


def iter_session_index() -> list[dict]:
    if not SESSION_INDEX_PATH.exists():
        return []
    items = []
    try:
        for line in SESSION_INDEX_PATH.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                items.append(item)
    except Exception:
        return []
    return items


def session_index_title(thread_id: str = "") -> str:
    latest = ""
    for item in iter_session_index():
        title = clean_subject(str(item.get("thread_name") or ""))
        if not title:
            continue
        if thread_id and str(item.get("id") or "") == thread_id:
            latest = title
        elif not thread_id:
            latest = title
    return latest


def session_index_id_for_title(title: str) -> str:
    target = clean_subject(title).casefold()
    if not target:
        return ""
    found = ""
    for item in iter_session_index():
        item_title = clean_subject(str(item.get("thread_name") or "")).casefold()
        if item_title == target:
            found = str(item.get("id") or "")
    return found


def notification_thread_id(notification: dict, task_name: str = "") -> str:
    for key in ("thread-id", "thread_id", "session-id", "session_id", "id"):
        if notification.get(key):
            return str(notification[key])
    return session_index_id_for_title(task_name)


def session_file_for_thread(thread_id: str):
    if not thread_id or not SESSIONS_ROOT.exists():
        return None
    try:
        matches = list(SESSIONS_ROOT.rglob(f"*{thread_id}*.jsonl"))
    except Exception:
        return None
    if not matches:
        return None
    return max(matches, key=lambda item: item.stat().st_mtime)


def text_from_message_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("input_text", "output_text", "text"):
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part.strip())


def session_last_user_and_assistant(thread_id: str):
    path = session_file_for_thread(thread_id)
    if not path:
        return "", ""
    last_user = ""
    last_assistant = ""
    last_final = ""
    try:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except Exception:
                continue
            if item.get("type") != "response_item":
                continue
            payload = item.get("payload") or {}
            if payload.get("type") != "message":
                continue
            role = payload.get("role")
            text = text_from_message_content(payload.get("content")).strip()
            if not text:
                continue
            if role == "user":
                cleaned = sanitize_user_instruction(text)
                if cleaned and not looks_like_instruction_or_path(cleaned):
                    last_user = cleaned
            elif role == "assistant":
                last_assistant = text
                if payload.get("phase") == "final_answer":
                    last_final = text
    except Exception:
        return "", ""
    return last_user, last_final or last_assistant


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

    for key in ("task-name", "task_name"):
        if notification.get(key):
            task = clean_subject(str(notification[key]))
            if not looks_like_instruction_or_path(task):
                return task

    user_messages = notification.get("input-messages") or []
    if user_messages:
        instruction = sanitize_user_instruction(first_non_empty_line(str(user_messages[0])), 90)
        if instruction and not looks_like_instruction_or_path(instruction):
            return clean_subject(instruction)
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
    resolved_task_name = task_name or resolve_task_name(notification, config)
    thread_id = notification_thread_id(notification, resolved_task_name)
    lines = [
        f"模型: {resolve_model_label(notification, codex_config)}",
        f"项目: {short_project_label(resolve_project_label(cwd, codex_config))}",
        f"任务: {resolved_task_name}",
    ]
    if thread_id and config.get("include_thread_id_in_body", True):
        lines.append(f"任务ID: {thread_id}")
    return lines


def metadata_block(notification: dict, config: dict, task_name: str = "") -> str:
    return "\n".join(metadata_lines(notification, config, task_name))


def compact_metadata_lines(notification: dict, config: dict, task_name: str = "") -> list[str]:
    codex_config = load_codex_config()
    cwd = notification.get("cwd") or config.get("codex_cwd") or ""
    resolved_task_name = task_name or resolve_task_name(notification, config)
    lines = [
        f"任务: {resolved_task_name}",
        f"项目: {short_project_label(resolve_project_label(cwd, codex_config))}",
        f"模型: {resolve_model_label(notification, codex_config)}",
    ]
    thread_id = notification_thread_id(notification, resolved_task_name)
    if thread_id and config.get("include_thread_id_in_body", True):
        lines.append(f"ID: {thread_id}")
    return lines


def compact_metadata_block(notification: dict, config: dict, task_name: str = "") -> str:
    return "\n".join(compact_metadata_lines(notification, config, task_name))


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


def sanitize_user_instruction(value: str, limit: int = 600) -> str:
    text = str(value or "")
    text = re.sub(r"(?is)<image\b.*?</image>", "", text)
    text = re.sub(r"(?is)<image\b[^>]*>", "", text)
    text = re.sub(r"(?ms)^# Files mentioned by the user:.*?(?=^## My request for Codex:|\Z)", "", text)
    text = text.replace("## My request for Codex:", "")
    text = re.sub(r"(?m)^.*\\.(png|jpg|jpeg|gif|webp):\s+.*$", "", text, flags=re.IGNORECASE)
    return compact_text(text, limit)


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
    for message in reversed(messages):
        text = sanitize_user_instruction(str(message), limit)
        if not text:
            continue
        if any(text.lower().startswith(prefix) for prefix in internal_prefixes):
            continue
        if looks_like_instruction_or_path(text):
            continue
        return text
    return "（空）"


def resolve_user_instruction(notification: dict, config: dict, task_name: str) -> str:
    user_messages = notification.get("input-messages") or []
    summary = user_instruction_summary(user_messages, 600)
    if summary != "（空）":
        return summary

    if not config.get("allow_session_history_instruction_fallback", False):
        return "（未从本次 Codex 通知中取得真实用户指令）"

    thread_id = notification_thread_id(notification, task_name)
    last_user, _last_assistant = session_last_user_and_assistant(thread_id)
    if last_user:
        return truncate_text(last_user, 600)
    return "（未从本次 Codex 通知中取得真实用户指令）"


def resolve_assistant_result(notification: dict, config: dict, task_name: str) -> str:
    assistant_message = str(notification.get("last-assistant-message") or "").strip()
    if assistant_message:
        return assistant_message

    if not config.get("allow_session_history_result_fallback", False):
        return "（未从本次 Codex 通知中取得结果）"

    thread_id = notification_thread_id(notification, task_name)
    _last_user, last_assistant = session_last_user_and_assistant(thread_id)
    return last_assistant.strip() or "（未从本次 Codex 通知中取得结果）"


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
        or "回复本邮件继续；新任务主题写 [codex-next]。"
    )


def build_body(notification: dict, config: dict) -> str:
    task_name = resolve_task_name(notification, config)
    user_instruction = resolve_user_instruction(notification, config, task_name)
    assistant_message = resolve_assistant_result(notification, config, task_name)
    cwd = notification.get("cwd", "")
    evidence = git_status_report(cwd, config)
    body_parts = [
        f"Codex 报告  {datetime.now().strftime('%m-%d %H:%M')}",
        compact_metadata_block(notification, config, task_name),
        "",
        "指令:",
        user_instruction,
    ]
    if evidence:
        body_parts.extend(["", "电脑端:", evidence])
    body_parts.extend(
        [
            "",
            "结果:",
            truncate_text(assistant_message.strip() or "（空）", 5000),
            "",
            "回复:",
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
    task_name = resolve_task_name(notification, config)
    subject = build_report_subject(notification, config)
    thread_id = notification_thread_id(notification, task_name)
    message_id = make_msgid(idstring=thread_id or "codex", domain="codex-mail-bridge.local")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["X-Codex-Mail-Bridge"] = "report"
    if thread_id:
        message["X-Codex-Thread-ID"] = thread_id
    message["X-Codex-Task-Name"] = task_name
    message.set_content(build_body(notification, config), charset="utf-8", cte="base64")

    with smtplib.SMTP(config.get("smtp_host", "smtp.gmail.com"), int(config.get("smtp_port", 587)), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(message)
    remember_mail_route(message_id, notification, config, subject, task_name)
    log(f"mail sent to {', '.join(recipients)}; subject={subject}")


def main() -> int:
    if os.environ.get("CODEX_MAIL_BRIDGE_SUPPRESS_NOTIFY") == "1":
        return 0

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
