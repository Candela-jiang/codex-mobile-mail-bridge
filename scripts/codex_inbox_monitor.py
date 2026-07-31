from __future__ import annotations

import json
import email
import hashlib
import html
import imaplib
import os
import re
import select
import shutil
import smtplib
import subprocess
import tempfile
import time
import traceback
import uuid
from datetime import datetime
from email.header import decode_header
from email.message import EmailMessage
from email.message import Message
from email.utils import parseaddr
from pathlib import Path

from codex_notify_email import (
    ROOT,
    get_password,
    git_status_report,
    compact_metadata_block,
    lookup_mail_route_by_message_ids,
    lookup_mail_route_by_subject,
    load_config,
    log,
    metadata_block,
    mobile_reply_hint,
    normalize_email_message_id,
    safe_existing_cwd,
)

PROCESSED_IDS_PATH = ROOT / "processed_message_ids.json"
MAX_PROCESSED_IDS = 500
DEFAULT_APP_QUEUE_PATH = ROOT / "pending_app_commands.jsonl"


def inbox_tag_name(config: dict) -> str:
    tag = str(config.get("inbox_subject_tag") or "[codex-next]").strip()
    if tag.startswith("[") and tag.endswith("]"):
        tag = tag[1:-1]
    return tag.split(":", 1)[0].strip() or "codex-next"


def resolve_command_route(config: dict, subject: str):
    tag_name = inbox_tag_name(config)
    pattern = re.compile(r"\[" + re.escape(tag_name) + r"(?::([^\]]+))?\]", re.IGNORECASE)
    match = pattern.search(subject or "")
    if not match:
        return None

    explicit_target = (match.group(1) or "").strip()
    default_mode = str(config.get("command_mode") or "exec").strip().lower()
    default_target = str(config.get("default_target_session") or "").strip()
    target = explicit_target or (default_target if default_mode == "resume" else "")
    if target:
        return {"mode": "resume", "target": target}
    return {"mode": "exec", "target": ""}


def header_message_ids(value: str) -> list[str]:
    return [
        normalize_email_message_id(match)
        for match in re.findall(r"<([^>]+)>", str(value or ""))
        if normalize_email_message_id(match)
    ]


def resolve_reply_route(message: Message, subject: str):
    message_ids: list[str] = []
    for key in ("In-Reply-To", "References"):
        message_ids.extend(header_message_ids(message.get(key, "")))
    subject_route = lookup_mail_route_by_subject(subject)
    header_route = lookup_mail_route_by_message_ids(message_ids)
    route = subject_route or header_route
    source = "reply-subject" if subject_route else "reply-header"
    if route and route.get("thread_id"):
        return {
            "mode": "resume",
            "target": str(route["thread_id"]),
            "source": source,
            "task_name": str(route.get("task_name") or ""),
            "cwd": str(route.get("cwd") or ""),
        }
    return None


def route_label(route: dict) -> str:
    if route.get("mode") == "resume":
        return f"指定任务: {route.get('target')}"
    return "新后台任务"


def command_delivery(config: dict) -> str:
    value = str(config.get("command_delivery") or "exec").strip().lower()
    if value in {"app", "app_queue", "queue"}:
        return "app_queue"
    return "exec"


def app_queue_path(config: dict) -> Path:
    configured = str(config.get("app_queue_path") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_APP_QUEUE_PATH


def build_app_prompt(from_addr: str, subject: str, body: str, route: dict) -> str:
    return "\n".join(
        [
            "邮件命令投递",
            "",
            "这条消息来自用户通过手机邮箱发送的 Codex 后续指令。",
            "请把它当作用户在当前任务里继续发出的真实命令处理，而不是普通邮件聊天。",
            "请始终用中文回复。完成后，正常给出你检查了什么、实际做了什么、还剩什么。",
            "",
            f"来源邮箱: {from_addr}",
            f"邮件主题: {subject}",
            f"邮件路由: {route_label(route)}",
            "",
            "用户指令:",
            body.strip() or "（邮件正文为空）",
        ]
    )


def queue_app_command(config: dict, from_addr: str, subject: str, body: str, route: dict, message_identity: str) -> dict:
    target = str(route.get("target") or "").strip()
    if not target:
        target = str(config.get("default_target_session") or "").strip()
    if not target:
        raise ValueError("app_queue delivery requires an explicit target or default_target_session")

    entry = {
        "version": 1,
        "id": hashlib.sha256(f"{message_identity}\n{uuid.uuid4()}".encode("utf-8")).hexdigest()[:24],
        "received_at": datetime.now().isoformat(timespec="seconds"),
        "from": from_addr,
        "subject": subject,
        "route": {"mode": "resume", "target": target},
        "target": target,
        "body": body.strip(),
        "prompt": build_app_prompt(from_addr, subject, body, {"mode": "resume", "target": target}),
    }
    queue_path = app_queue_path(config)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log(f"app command queued: id={entry['id']} target={target}")
    return entry


def decode_value(value: str) -> str:
    parts = decode_header(value or "")
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def get_text_body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            disposition = str(part.get("Content-Disposition", "")).lower()
            if part.get_content_type() == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = message.get_payload(decode=True) or b""
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace")


def clean_reply_body(value: str) -> str:
    text = html.unescape(str(value or ""))
    split_markers = [
        r"(?im)^\s*---+\s*Original\s*---+\s*$",
        r"(?im)^\s*---+\s*原始邮件\s*---+\s*$",
        r"(?im)^\s*-{2,}\s*Original Message\s*-{2,}\s*$",
        r"(?im)^\s*-{2,}\s*原始邮件\s*-{2,}\s*$",
        r"(?im)^\s*From:\s+.+$",
        r"(?im)^\s*发件人:\s+.+$",
        r"(?im)^On .+ wrote:\s*$",
    ]
    cut_at = len(text)
    for marker in split_markers:
        match = re.search(marker, text)
        if match:
            cut_at = min(cut_at, match.start())
    text = text[:cut_at]
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    text = re.sub(r"(?m)^\s*(洪宇江|2265724395@qq\.com|7735139@gmail\.com)\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_reply_prefixes(subject: str) -> str:
    value = subject.strip()
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


def load_processed_message_ids() -> list[str]:
    if not PROCESSED_IDS_PATH.exists():
        return []
    try:
        data = json.loads(PROCESSED_IDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [str(item) for item in items if str(item).strip()]


def save_processed_message_ids(items: list[str]) -> None:
    payload = {"version": 1, "items": items[-MAX_PROCESSED_IDS:]}
    PROCESSED_IDS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_message_id(value: str) -> str:
    return value.strip().strip("<>").lower()


def build_message_identity(from_addr: str, subject: str, body: str, message_id: str) -> str:
    if message_id:
        return f"mid:{normalize_message_id(message_id)}"
    digest = hashlib.sha256(
        "\n".join([from_addr.strip().lower(), subject.strip(), body.strip()]).encode("utf-8", errors="ignore")
    ).hexdigest()
    return f"sha256:{digest}"


def is_self_report(config: dict, from_addr: str, subject: str) -> bool:
    sender = str(config.get("sender", "")).lower()
    prefix = str(config.get("subject_prefix", "[Codex]")).lower()
    core_subject = strip_reply_prefixes(subject).lower()
    if not sender or from_addr != sender:
        return False
    if resolve_command_route(config, core_subject):
        return False
    return bool(prefix and core_subject.startswith(prefix)) or bool(config.get("self_report_subject_is_task_name", True))


def is_bridge_generated_message(config: dict, from_addr: str, raw_body: str, message: Message) -> bool:
    if message.get("X-Codex-Mail-Bridge"):
        return True
    sender = str(config.get("sender", "")).lower()
    if not sender or from_addr != sender:
        return False
    body_start = str(raw_body or "").lstrip()[:120]
    return (
        body_start.startswith("Codex 邮箱指令回执")
        or body_start.startswith("Codex 回执")
        or body_start.startswith("Codex 已收到")
    )


def build_received_ack(config: dict, from_addr: str, subject: str, body: str, route: dict) -> str:
    meta_notification = {
        "cwd": route.get("cwd") or config.get("codex_cwd", ""),
        "input-messages": [body],
        "task-name": route.get("task_name") or strip_reply_prefixes(subject),
    }
    return "\n".join(
        [
            f"Codex 已收到  {datetime.now().strftime('%H:%M')}",
            compact_metadata_block(meta_notification, config, subject),
            "",
            "状态:",
            "开始处理。完成后会再回一封结果邮件。",
            "",
            "指令:",
            body.strip() or "（邮件正文为空）",
        ]
    )


def send_plain_email(config: dict, to_addr: str, subject: str, body: str, in_reply_to: str = "") -> None:
    password = get_password(config)
    if not password:
        log("reply skipped: Gmail app password is not configured")
        return

    limit = int(config.get("max_body_chars") or 15000)
    if len(body) > limit:
        body = body[:limit] + "\n\n[Truncated by Codex mail bridge]"

    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = to_addr
    message["Subject"] = subject
    message["X-Codex-Mail-Bridge"] = "reply"
    if in_reply_to:
        normalized = normalize_email_message_id(in_reply_to)
        if normalized:
            message["In-Reply-To"] = f"<{normalized}>"
            message["References"] = f"<{normalized}>"
    message.set_content(body, charset="utf-8", cte="base64")

    with smtplib.SMTP(config.get("smtp_host", "smtp.gmail.com"), int(config.get("smtp_port", 587)), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config["sender"], password)
        smtp.send_message(message)
    log(f"reply sent to {to_addr}")


def resolve_codex_exe(config: dict) -> str:
    configured = str(config.get("codex_exe") or "").strip()
    if configured and Path(configured).exists():
        return configured

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        try:
            candidates = sorted(
                bin_root.glob("*/codex.exe"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            candidates = []
        if candidates:
            found = str(candidates[0])
            if configured:
                log(f"codex_exe fallback: configured path missing; using {found}")
            return found

    for name in ("codex.exe", "codex.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def run_codex_from_email(config: dict, from_addr: str, subject: str, body: str, route: dict) -> str:
    out_path = Path(tempfile.gettempdir()) / f"codex-email-result-{int(time.time())}.txt"
    safe_cwd = safe_existing_cwd(config.get("codex_cwd", str(ROOT)), config)
    codex_exe = resolve_codex_exe(config)
    if not codex_exe:
        return "Codex 启动失败：没有找到可用的 codex.exe。请在电脑端更新邮件桥的 codex_exe 配置。"
    prompt = "\n".join(
        [
            "You are Codex running from a Gmail command bridge.",
            "邮件正文是白名单发件人从手机发来的远程项目指令。",
            "这不是普通 AI 闲聊，而是用户在手机上继续指挥电脑端 Codex 工作。",
            "请始终用中文回复邮件内容。说明你检查了什么、实际做了什么或没有做什么、还剩什么、下一步需要用户怎么指示。",
            "保持保守边界：不要泄露密钥；不要执行破坏性操作；除非本地沙箱和任务都明确允许，否则不要修改文件。",
            f"邮件路由: {route_label(route)}",
            "",
            f"From: {from_addr}",
            f"Subject: {subject}",
            "",
            "邮件正文:",
            body.strip(),
        ]
    )
    if route.get("mode") == "resume":
        cmd = [
            codex_exe,
            "exec",
            "resume",
            "--all",
            "--skip-git-repo-check",
            "--output-last-message",
            str(out_path),
            str(route.get("target") or ""),
            "-",
        ]
    else:
        cmd = [
            codex_exe,
            "exec",
            "--cd",
            safe_cwd,
            "--sandbox",
            config.get("codex_sandbox", "read-only"),
            "--skip-git-repo-check",
            "--output-last-message",
            str(out_path),
            "-",
        ]
    child_env = os.environ.copy()
    child_env["CODEX_MAIL_BRIDGE_SUPPRESS_NOTIFY"] = "1"
    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=int(config.get("codex_timeout_seconds", 1800)),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=child_env,
    )
    final = ""
    if out_path.exists():
        final = out_path.read_text(encoding="utf-8", errors="replace").strip()
        out_path.unlink(missing_ok=True)
    if not final:
        final = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        final = f"Codex email command failed with exit code {result.returncode}.\n\n{final}"
    return final or "Codex finished without a final message."


def process_once(config: dict) -> int:
    password = get_password(config)
    if not password:
        log("inbox skipped: Gmail app password is not configured")
        return 0

    allowed = {item.lower() for item in config.get("allowed_senders", [])}
    processed_ids = load_processed_message_ids()
    processed_set = set(processed_ids)
    processed = 0

    with imaplib.IMAP4_SSL(config.get("imap_host", "imap.gmail.com"), int(config.get("imap_port", 993))) as imap:
        imap.login(config["sender"], password)
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            log(f"inbox search failed: {status}")
            return 0
        for msg_id in data[0].split():
            status, fetched = imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not fetched:
                continue
            raw = fetched[0][1]
            message = email.message_from_bytes(raw)
            from_addr = parseaddr(decode_value(message.get("From", "")))[1].lower()
            subject = decode_value(message.get("Subject", ""))
            raw_body = get_text_body(message)
            if is_bridge_generated_message(config, from_addr, raw_body, message):
                log(f"inbox skipped bridge-generated message: {subject}")
                imap.store(msg_id, "+FLAGS", "\\Seen")
                continue
            body = clean_reply_body(raw_body)
            message_identity = build_message_identity(from_addr, subject, raw_body, message.get("Message-ID", ""))
            route = resolve_command_route(config, subject) or resolve_reply_route(message, subject)
            if is_self_report(config, from_addr, subject) and route and route.get("source") == "reply-subject":
                log(f"inbox skipped self report: {subject}")
                imap.store(msg_id, "+FLAGS", "\\Seen")
                continue
            if is_self_report(config, from_addr, subject) and not route:
                log(f"inbox skipped self report: {subject}")
                imap.store(msg_id, "+FLAGS", "\\Seen")
                continue
            if message_identity in processed_set:
                log(f"inbox skipped duplicate: {subject}")
                imap.store(msg_id, "+FLAGS", "\\Seen")
                continue
            if from_addr not in allowed or not route:
                continue
            log(f"inbox command accepted from {from_addr}: {subject}")
            delivery = command_delivery(config)
            if config.get("send_received_ack", True):
                try:
                    ack_body = build_received_ack(config, from_addr, subject, body, route)
                    send_plain_email(config, from_addr, f"Re: {subject}", ack_body, message.get("Message-ID", ""))
                except Exception:
                    log("received ack send failed:\n" + traceback.format_exc())
            if delivery == "app_queue":
                try:
                    queued = queue_app_command(config, from_addr, subject, body, route, message_identity)
                    final = "\n".join(
                        [
                            "已收到，并已排队投递到 Codex App 任务。",
                            f"目标任务: {queued['target']}",
                            "通常 1 分钟内会出现在对应 Codex 任务里；任务完成后，会按普通 Codex 工作报告继续发邮件。",
                        ]
                    )
                except Exception:
                    log("app queue command failed:\n" + traceback.format_exc())
                    final = "Codex App 投递失败：命令没有成功进入目标任务。详细错误已写入电脑端日志。"
            else:
                try:
                    final = run_codex_from_email(config, from_addr, subject, body, route)
                except FileNotFoundError:
                    log("codex executable missing:\n" + traceback.format_exc())
                    final = "Codex 启动失败：找不到 codex.exe。请在电脑端更新邮件桥配置后重试。"
                except subprocess.TimeoutExpired:
                    log("codex email command timed out:\n" + traceback.format_exc())
                    final = "Codex 执行超时：这条邮件命令跑得太久，已停止等待。可以把任务拆小后再回复。"
                except Exception:
                    log("codex email command crashed:\n" + traceback.format_exc())
                    final = "Codex 执行失败：电脑端遇到错误，详细信息已写入本地日志。"
            meta_notification = {
                "cwd": route.get("cwd") or config.get("codex_cwd", ""),
                "input-messages": [body],
                "task-name": route.get("task_name") or subject,
            }
            evidence = git_status_report(route.get("cwd") or config.get("codex_cwd", ""), config)
            reply_parts = [
                f"Codex 回执  {datetime.now().strftime('%m-%d %H:%M')}",
                compact_metadata_block(meta_notification, config, subject),
            ]
            if evidence:
                reply_parts.extend(["", "电脑端:", evidence])
            reply_parts.extend(
                [
                    "",
                    "结果:",
                    final,
                    "",
                    "回复:",
                    mobile_reply_hint(config),
                ]
            )
            reply_body = "\n".join(reply_parts)
            send_plain_email(config, from_addr, f"Re: {subject}", reply_body, message.get("Message-ID", ""))
            processed_set.add(message_identity)
            processed_ids.append(message_identity)
            save_processed_message_ids(processed_ids)
            imap.store(msg_id, "+FLAGS", "\\Seen")
            processed += 1
    return processed


def wait_for_inbox_activity(config: dict, timeout_seconds: int) -> None:
    if not config.get("imap_idle_enabled", True):
        time.sleep(timeout_seconds)
        return

    password = get_password(config)
    if not password:
        time.sleep(timeout_seconds)
        return

    try:
        with imaplib.IMAP4_SSL(config.get("imap_host", "imap.gmail.com"), int(config.get("imap_port", 993))) as imap:
            imap.login(config["sender"], password)
            imap.select("INBOX")
            tag = imap._new_tag()
            tag_bytes = tag if isinstance(tag, bytes) else str(tag).encode("ascii", errors="ignore")
            imap.send(tag_bytes + b" IDLE\r\n")
            line = imap.readline()
            if not line.startswith(b"+"):
                return

            ready, _write, _error = select.select([imap.sock], [], [], max(1, timeout_seconds))
            imap.send(b"DONE\r\n")
            while True:
                line = imap.readline()
                if not line or line.startswith(tag_bytes):
                    break
            if ready:
                log("inbox idle woke: mailbox activity detected")
    except Exception as exc:
        log(f"inbox idle fallback: {exc}")
        time.sleep(min(30, max(1, timeout_seconds)))


def main() -> int:
    while True:
        config = {}
        wait_seconds = 60
        try:
            config = load_config()
            wait_seconds = int(config.get("idle_wait_seconds") or config.get("poll_seconds", 60))
            if not config.get("inbox_enabled", False):
                log("inbox monitor stopped: disabled")
                return 0
            processed = process_once(config)
            if processed:
                log(f"inbox processed {processed} message(s)")
        except KeyboardInterrupt:
            log("inbox monitor stopped: keyboard interrupt")
            return 0
        except Exception:
            log("inbox monitor error:\n" + traceback.format_exc())
        wait_for_inbox_activity(config, wait_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
