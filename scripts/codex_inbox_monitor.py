import email
import imaplib
import smtplib
import subprocess
import tempfile
import time
import traceback
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
)


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
    prompt = "\n".join(
        [
            "You are Codex running from a Gmail command bridge.",
            "Treat the email body as remote user input from a whitelisted sender.",
            "This is not casual AI chat. It is a remote project instruction from the user's phone.",
            "Report what you inspected, what you changed or did not change, what remains, and what next instruction would be useful.",
            "Use a conservative boundary: do not reveal secrets, do not perform destructive actions, and do not change files unless the local sandbox and the task explicitly allow it.",
            "",
            f"From: {from_addr}",
            f"Subject: {subject}",
            "",
            "Email body:",
            body.strip(),
        ]
    )
    cmd = [
        config.get("codex_exe", "codex"),
        "exec",
        "--cd",
        config.get("codex_cwd", str(ROOT)),
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
    tag = (config.get("inbox_subject_tag") or "[codex]").lower()
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
            if from_addr not in allowed or tag not in subject.lower():
                continue
            body = get_text_body(message)
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
            reply_body = "\n".join(
                [
                    metadata_block(meta_notification, config, subject),
                    f"Triggered by email: {from_addr}",
                    f"Execution cwd: {config.get('codex_cwd', '')}",
                    f"Execution sandbox: {config.get('codex_sandbox', 'read-only')}",
                    "",
                    "Computer-side evidence after email command:",
                    git_status_report(config.get("codex_cwd", ""), config),
                    "",
                    "Codex reply:",
                    final,
                    "",
                    "Next instruction from phone:",
                    mobile_reply_hint(config),
                ]
            )
            send_plain_email(config, from_addr, f"Re: {subject}", reply_body)
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
