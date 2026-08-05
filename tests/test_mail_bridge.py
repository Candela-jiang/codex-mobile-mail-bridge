from __future__ import annotations

import json
import sys
import unittest
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import codex_inbox_monitor as inbox  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_manifest_is_plugin_eval_friendly(self) -> None:
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        interface = manifest["interface"]

        self.assertIsInstance(interface["defaultPrompt"], list)
        self.assertLessEqual(len(interface["defaultPrompt"]), 3)
        self.assertIn("websiteURL", interface)
        self.assertIn("privacyPolicyURL", interface)
        self.assertIn("termsOfServiceURL", interface)

        for key in ("logo", "composerIcon"):
            rel = interface[key]
            self.assertTrue(rel.startswith("./"))
            self.assertTrue((ROOT / rel[2:]).exists(), rel)

    def test_example_config_keeps_safe_defaults(self) -> None:
        config = json.loads((SCRIPTS / "config.example.json").read_text(encoding="utf-8"))

        self.assertEqual(config["codex_sandbox"], "read-only")
        self.assertIsInstance(config["email_command_model"], str)
        self.assertFalse(config["send_received_ack"])
        self.assertEqual(config["command_delivery"], "exec")
        self.assertEqual(config["allowed_senders"], ["your-address@gmail.com"])


class InboxRoutingTests(unittest.TestCase):
    def test_reply_subject_prefers_task_name(self) -> None:
        subject = "Re: [codex-next:019fb407-95d7] 现在显示现在的时间"
        route = {"mode": "resume", "target": "019fb407-95d7", "task_name": "Space-Marines"}

        self.assertEqual(inbox.reply_subject_for_route(route, subject), "Space-Marines")

    def test_reply_subject_fallback_strips_reply_prefixes(self) -> None:
        route = {"mode": "resume", "target": "demo"}

        self.assertEqual(
            inbox.reply_subject_for_route(route, "Re: Re: codex-mobile-mail-bridge"),
            "codex-mobile-mail-bridge",
        )

    def test_clean_reply_body_removes_quoted_original_mail(self) -> None:
        body = "继续优化邮箱主题\r\n\r\n---原始邮件---\r\n旧内容不应该被再次执行"

        self.assertEqual(inbox.clean_reply_body(body), "继续优化邮箱主题")

    def test_resolve_command_route_supports_default_resume(self) -> None:
        config = {
            "inbox_subject_tag": "[codex-next]",
            "command_mode": "resume",
            "default_target_session": "019fb407-95d7",
        }

        route = inbox.resolve_command_route(config, "Re: [codex-next]")

        self.assertEqual(route, {"mode": "resume", "target": "019fb407-95d7"})

    def test_resolve_command_route_explicit_target_wins(self) -> None:
        config = {
            "inbox_subject_tag": "[codex-next]",
            "command_mode": "resume",
            "default_target_session": "default",
        }

        route = inbox.resolve_command_route(config, "[codex-next:Space-Marines]")

        self.assertEqual(route, {"mode": "resume", "target": "Space-Marines"})

    def test_stale_unseen_messages_are_ignored_by_default(self) -> None:
        message = EmailMessage()
        message["Date"] = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).strftime("%a, %d %b %Y %H:%M:%S +0000")

        self.assertTrue(inbox.is_stale_unseen_message({}, message))
        self.assertFalse(
            inbox.is_stale_unseen_message({"ignore_unseen_before_start": False}, message)
        )


if __name__ == "__main__":
    unittest.main()
