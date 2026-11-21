from __future__ import annotations

import unittest

import support  # noqa: F401
from support import utc

from mailbox_watcher.imap import MessageSummary
from mailbox_watcher.report import (
    MAX_LISTED_MESSAGES,
    MailboxStatus,
    build_failure_alert,
    build_heartbeat,
    build_report,
    format_utc,
)
from mailbox_watcher.window import ReportWindow

WINDOW = ReportWindow(utc(2026, 11, 21, 6), utc(2026, 11, 21, 12))


def message(subject=None, folder="INBOX", minute=12):
    return MessageSummary(
        folder=folder,
        received=utc(2026, 11, 21, 7, minute),
        sender="Alice <alice@example.com>",
        recipients="owner@example.org",
        date="Sat, 21 Nov 2026 08:12:00 +0100",
        subject=subject,
    )


class ReportTests(unittest.TestCase):
    def test_report_contains_metadata(self):
        report = build_report("owner-example", ["ceo@example.com"], WINDOW, [message()], multiple_folders=False)
        self.assertEqual(report.recipients, ("ceo@example.com",))
        self.assertEqual(report.subject, "[mailbox-watcher] 1 new message in owner-example")
        self.assertIn("Window:  2026-11-21 06:00 UTC to 2026-11-21 12:00 UTC", report.text)
        self.assertIn("Received: 2026-11-21 07:12 UTC", report.text)
        self.assertIn("From:     Alice <alice@example.com>", report.text)
        self.assertIn("To:       owner@example.org", report.text)
        self.assertIn("Date:     Sat, 21 Nov 2026 08:12:00 +0100", report.text)
        self.assertIn("Message contents are not forwarded", report.text)

    def test_subject_line_absent_unless_fetched(self):
        report = build_report("owner-example", ["ceo@example.com"], WINDOW, [message()], multiple_folders=False)
        self.assertNotIn("Subject:", report.text)
        with_subject = build_report(
            "owner-example", ["ceo@example.com"], WINDOW, [message(subject="Hello")], multiple_folders=False
        )
        self.assertIn("Subject:  Hello", with_subject.text)

    def test_email_subject_never_contains_message_subject(self):
        report = build_report(
            "owner-example", ["ceo@example.com"], WINDOW, [message(subject="Code 123456")], multiple_folders=False
        )
        self.assertNotIn("123456", report.subject)

    def test_plural_and_folder(self):
        report = build_report(
            "owner-example",
            ["ceo@example.com"],
            WINDOW,
            [message(folder="INBOX"), message(folder="Junk", minute=30)],
            multiple_folders=True,
        )
        self.assertEqual(report.subject, "[mailbox-watcher] 2 new messages in owner-example")
        self.assertIn("Folder:   Junk", report.text)
        self.assertIn("2. Received", report.text)

    def test_folder_hidden_for_single_folder(self):
        report = build_report("owner-example", ["ceo@example.com"], WINDOW, [message()], multiple_folders=False)
        self.assertNotIn("Folder:", report.text)

    def test_empty_values_are_marked(self):
        empty = MessageSummary("INBOX", utc(2026, 11, 21, 7), "", "", "", "")
        report = build_report("owner-example", ["ceo@example.com"], WINDOW, [empty], multiple_folders=False)
        self.assertIn("From:     (none)", report.text)
        self.assertIn("Subject:  (none)", report.text)

    def test_long_reports_are_truncated(self):
        messages = [message() for _ in range(MAX_LISTED_MESSAGES + 5)]
        report = build_report("owner-example", ["ceo@example.com"], WINDOW, messages, multiple_folders=False)
        self.assertIn(f"{MAX_LISTED_MESSAGES}. Received", report.text)
        self.assertNotIn(f"{MAX_LISTED_MESSAGES + 1}. Received", report.text)
        self.assertIn("... and 5 more.", report.text)
        self.assertIn(f"{MAX_LISTED_MESSAGES + 5} new messages", report.subject)


class AlertTests(unittest.TestCase):
    def test_failure_alert(self):
        alert = build_failure_alert("owner-example", ["ceo@example.com"], WINDOW, "authentication failed")
        self.assertEqual(alert.subject, "[mailbox-watcher] Check failed for owner-example")
        self.assertIn("Problem: authentication failed", alert.text)
        self.assertIn("will not be reported later", alert.text)


class HeartbeatTests(unittest.TestCase):
    def test_all_ok(self):
        heartbeat = build_heartbeat(
            ["ceo@example.com"], WINDOW, [MailboxStatus("a", True, 2), MailboxStatus("b", True, 0)]
        )
        self.assertEqual(heartbeat.subject, "[mailbox-watcher] Heartbeat: all ok")
        self.assertIn("- a: ok, 2 new", heartbeat.text)
        self.assertIn("Mailboxes checked: 2, failed: 0", heartbeat.text)

    def test_with_failures(self):
        heartbeat = build_heartbeat(
            ["ceo@example.com"], WINDOW, [MailboxStatus("a", False, 0, "connection failed")]
        )
        self.assertEqual(heartbeat.subject, "[mailbox-watcher] Heartbeat: 1 failed")
        self.assertIn("- a: FAILED (connection failed)", heartbeat.text)


class FormatTests(unittest.TestCase):
    def test_format_utc(self):
        self.assertEqual(format_utc(utc(2026, 1, 2, 3, 4)), "2026-01-02 03:04 UTC")


if __name__ == "__main__":
    unittest.main()
