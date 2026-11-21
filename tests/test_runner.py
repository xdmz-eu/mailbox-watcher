from __future__ import annotations

import json
import unittest

import support  # noqa: F401
from support import (
    BASE_CONFIG,
    PROJECT_ID,
    FakeImap,
    FakeImapFactory,
    FakeMessage,
    RecordingSender,
    config,
    config_json,
    header_block,
    utc,
)

from mailbox_watcher.config import CONFIG_ENV, ConfigError, parse
from mailbox_watcher.runner import run, run_from_environment, tem_sender_from_environment
from mailbox_watcher.tem import TemSender

NOW = utc(2026, 11, 21, 12, 5)
ENV = {"MAILBOX_OWNER_EXAMPLE_PASSWORD": "pw"}


def second_mailbox(**overrides):
    box = {
        "name": "owner-second",
        "host": "imap.example.com",
        "username": "owner@example.com",
        "password_env": "MAILBOX_SECOND_PASSWORD",
        "notify": ["ceo2@example.com"],
    }
    box.update(overrides)
    return box


def inbox(*messages):
    return FakeImap({"INBOX": list(messages)})


def message_at(time="21-Nov-2026 08:00:00 +0000", **headers):
    return FakeMessage(1, time, header_block(**headers))


class RunTests(unittest.TestCase):
    def test_new_messages_produce_one_report_per_mailbox(self):
        settings = parse(config(mailboxes=[BASE_CONFIG["mailboxes"][0], second_mailbox()]))
        factory = FakeImapFactory(
            {
                "imap.example.org": inbox(message_at()),
                "imap.example.com": inbox(message_at(), FakeMessage(2, "21-Nov-2026 09:00:00 +0000", header_block())),
            }
        )
        sender = RecordingSender()
        result = run(settings, {**ENV, "MAILBOX_SECOND_PASSWORD": "pw2"}, NOW, sender, factory)

        self.assertTrue(result.ok)
        self.assertEqual([n.recipients for n in sender.sent], [("ceo@example.com",), ("ceo2@example.com",)])
        self.assertIn("1 new message in owner-example", sender.sent[0].subject)
        self.assertIn("2 new messages in owner-second", sender.sent[1].subject)
        self.assertEqual([s.message_count for s in result.statuses], [1, 2])

    def test_passwords_are_taken_from_named_environment_variables(self):
        settings = parse(config())
        imap = inbox()
        run(settings, ENV, NOW, RecordingSender(), FakeImapFactory({"imap.example.org": imap}))
        self.assertEqual(imap.calls[0], ("login", "owner@example.org", "pw"))

    def test_no_messages_sends_nothing(self):
        settings = parse(config())
        sender = RecordingSender()
        result = run(settings, ENV, NOW, sender, FakeImapFactory({"imap.example.org": inbox()}))
        self.assertTrue(result.ok)
        self.assertEqual(sender.sent, [])

    def test_messages_outside_window_are_ignored(self):
        settings = parse(config())
        sender = RecordingSender()
        run(settings, ENV, NOW, sender, FakeImapFactory({"imap.example.org": inbox(message_at("21-Nov-2026 12:01:00 +0000"))}))
        self.assertEqual(sender.sent, [])

    def test_missing_password_sends_alert(self):
        settings = parse(config())
        sender = RecordingSender()
        factory = FakeImapFactory({"imap.example.org": inbox()})
        result = run(settings, {}, NOW, sender, factory)
        self.assertFalse(result.ok)
        self.assertEqual(factory.opened, [])
        self.assertEqual(result.statuses[0].error, "password is not configured")
        self.assertIn("Check failed for owner-example", sender.sent[0].subject)
        self.assertEqual(sender.sent[0].recipients, ("ceo@example.com",))

    def test_failure_of_one_mailbox_does_not_stop_others(self):
        settings = parse(config(mailboxes=[BASE_CONFIG["mailboxes"][0], second_mailbox()]))
        factory = FakeImapFactory(
            {"imap.example.org": FakeImap(login_ok=False), "imap.example.com": inbox(message_at())}
        )
        sender = RecordingSender()
        result = run(settings, {**ENV, "MAILBOX_SECOND_PASSWORD": "pw2"}, NOW, sender, factory)
        self.assertFalse(result.ok)
        self.assertEqual([s.ok for s in result.statuses], [False, True])
        self.assertEqual(len(sender.sent), 2)
        self.assertIn("Check failed", sender.sent[0].subject)
        self.assertIn("new message", sender.sent[1].subject)

    def test_send_failures_are_recorded(self):
        settings = parse(config())
        sender = RecordingSender(fail_for={"new message"})
        result = run(settings, ENV, NOW, sender, FakeImapFactory({"imap.example.org": inbox(message_at())}))
        self.assertFalse(result.ok)
        self.assertEqual(result.send_failures, ["owner-example:report"])
        self.assertTrue(result.statuses[0].ok)

    def test_heartbeat_only_at_configured_boundary(self):
        settings = parse(config(heartbeat={"hour_utc": 12, "notify": ["ops@example.com"]}))
        for now, expected in ((utc(2026, 11, 21, 12, 5), True), (utc(2026, 11, 21, 18, 5), False)):
            with self.subTest(now=now):
                sender = RecordingSender()
                result = run(settings, ENV, now, sender, FakeImapFactory({"imap.example.org": inbox()}))
                self.assertEqual(result.heartbeat_sent, expected)
                heartbeats = [n for n in sender.sent if "Heartbeat" in n.subject]
                self.assertEqual(len(heartbeats), 1 if expected else 0)
                if expected:
                    self.assertEqual(heartbeats[0].recipients, ("ops@example.com",))

    def test_heartbeat_reports_failures(self):
        settings = parse(config(heartbeat={"hour_utc": 12, "notify": ["ops@example.com"]}))
        sender = RecordingSender()
        run(settings, {}, NOW, sender, FakeImapFactory({"imap.example.org": inbox()}))
        heartbeat = next(n for n in sender.sent if "Heartbeat" in n.subject)
        self.assertIn("1 failed", heartbeat.subject)

    def test_public_summary_contains_no_addresses_or_headers(self):
        settings = parse(config())
        result = run(
            settings,
            ENV,
            NOW,
            RecordingSender(),
            FakeImapFactory({"imap.example.org": inbox(message_at(sender="alice@example.com", subject="Code 1234"))}),
        )
        dumped = json.dumps(result.to_public_dict())
        for secret in ("@", "alice", "1234", "pw"):
            self.assertNotIn(secret, dumped)
        self.assertEqual(result.to_public_dict()["mailboxes"][0]["new_messages"], 1)
        self.assertEqual(result.to_public_dict()["window"]["end"], "2026-11-21T12:00:00+00:00")


class EnvironmentTests(unittest.TestCase):
    def test_run_from_environment(self):
        env = {CONFIG_ENV: config_json(), **ENV}
        sender = RecordingSender()
        result = run_from_environment(env, NOW, sender, FakeImapFactory({"imap.example.org": inbox(message_at())}))
        self.assertTrue(result.ok)
        self.assertEqual(len(sender.sent), 1)

    def test_run_from_environment_requires_config(self):
        with self.assertRaises(ConfigError):
            run_from_environment({}, NOW, RecordingSender())

    def test_tem_sender_from_environment(self):
        settings = parse(config())
        sender = tem_sender_from_environment(
            {"TEM_SECRET_KEY": "k", "TEM_PROJECT_ID": PROJECT_ID}, settings
        )
        self.assertIsInstance(sender, TemSender)
        self.assertIn("/regions/fr-par/", sender.url)

    def test_tem_sender_requires_secret(self):
        with self.assertRaises(ValueError):
            tem_sender_from_environment({"TEM_PROJECT_ID": PROJECT_ID}, parse(config()))


if __name__ == "__main__":
    unittest.main()
