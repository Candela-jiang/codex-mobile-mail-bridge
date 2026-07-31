from __future__ import annotations

import json
import email
import hashlib
import imaplib
import smtplib
import subprocess
import tempfile
import time
import traceback
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
    load_config,
    log,
    metadata_block,
    mobile_reply_hint,
    safe_existing_cwd,
)

PROCESSED_IDS_PATH = ROOT / "processed_message_ids.json"
MAX_PROCESSED_IDS = 500


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


def strip_reply_prefixes(subject: str) -> str:
    value = subject.strip()
    while True:
        lowered = value.lower()
        if lowered.startswith("re:") or lowered.startswith("fw:") or lowered.startswith("fwd:"):
            value = value.split(":", 1)[1].strip()
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
    return bool(sender and from_addr == sender and prefix and core_subject.startswith(prefix))


def send_plain_email(config: dict, to_addr: str, subject: str, body: str) -> None:
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
    message.set_content(body)

    with smtplib.SMTP(config.get("smtp_host", "smtp.gmail.com"), int(config.get("smtp_port", 587)), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config["sender"], password)
        smtp.send_message(message)
    log(f"reply sent to {to_addr}")


def run_codex_from_email(config: dict, from_addr: str, subject: str, body: str) -> str:
    out_path = Path(tempfile.gettempdir()) / f"codex-email-result-{int(time.time())}.txt"
    safe_cwd = safe_existing_cwd(config.get("codex_cwd", str(ROOT)), config)
    prompt = "\n".join(
        [
            "You are Codex running from a Gmail command bridge.",
            "邮件正文是白名单发件人从手机发来的远程项目指令。",
            "这不是普通 AI 闲聊，而是用户在手机上继续指挥电脑端 Codex 工作。",
            "请始终用中文回复邮件内容。说明你检查了什么、实际做了什么或没有做什么、还剩什么、下一步需要用户怎么指示。",
            "保持保守边界：不要泄露密钥；不要执行破坏性操作；除非本地沙箱和任务都明确允许，否则不要修改文件。",
            "",
            f"From: {from_addr}",
            f"Subject: {subject}",
            "",
            "邮件正文:",
            body.strip(),
        ]
    )
    cmd = [
        config.get("codex_exe", "codex"),
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
    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=int(config.get("codex_timeout_seconds", 1800)),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
    tag = (config.get("inbox_subject_tag") or "[codex-next]").lower()
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
            body = get_text_body(message)
            message_identity = build_message_identity(from_addr, subject, body, message.get("Message-ID", ""))
            if is_self_report(config, from_addr, subject):
                log(f"inbox skipped self report: {subject}")
                imap.store(msg_id, "+FLAGS", "\\Seen")
                continue
            if message_identity in processed_set:
                log(f"inbox skipped duplicate: {subject}")
                imap.store(msg_id, "+FLAGS", "\\Seen")
                continue
            if from_addr not in allowed or tag not in subject.lower():
                continue
            log(f"inbox command accepted from {from_addr}: {subject}")
            try:
                final = run_codex_from_email(config, from_addr, subject, body)
            except Exception:
                final = "Codex email command crashed:\n\n" + traceback.format_exc()
            meta_notification = {
                "cwd": config.get("codex_cwd", ""),
                "input-messages": [body],
                "task-name": subject,
            }
            evidence = git_status_report(config.get("codex_cwd", ""), config)
            reply_parts = [
                "Codex 邮箱指令回执",
                "",
                f"时间: {datetime.now().strftime('%m-%d %H:%M')}",
                metadata_block(meta_notification, config, subject),
                f"来源: {from_addr}",
            ]
            if evidence:
                reply_parts.extend(["", "电脑端:", evidence])
            reply_parts.extend(
                [
                    "",
                    "结果:",
                    final,
                    "",
                    "下一步:",
                    mobile_reply_hint(config),
                ]
            )
            reply_body = "\n".join(reply_parts)
            send_plain_email(config, from_addr, f"Re: {subject}", reply_body)
            processed_set.add(message_identity)
            processed_ids.append(message_identity)
            save_processed_message_ids(processed_ids)
            imap.store(msg_id, "+FLAGS", "\\Seen")
            processed += 1
    return processed


def main() -> int:
    while True:
        sleep_seconds = 60
        try:
            config = load_config()
            sleep_seconds = int(config.get("poll_seconds", 60))
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
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
