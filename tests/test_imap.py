from __future__ import annotations

import imaplib
import socket
import ssl
import unittest

import support  # noqa: F401
from support import BASE_CONFIG, FakeImap, FakeImapFactory, FakeMessage, header_block, utc

from mailbox_watcher.config import parse
from mailbox_watcher.imap import (
    MailboxCheckError,
    collect_messages,
    imap_date,
    parse_internaldate,
    quote_mailbox_name,
    sanitize,
)
from mailbox_watcher.window import ReportWindow

WINDOW = ReportWindow(utc(2026, 11, 21, 6), utc(2026, 11, 21, 12))


def make_mailbox(**overrides):
    data = dict(BASE_CONFIG)
    box = dict(BASE_CONFIG["mailboxes"][0])
    box.update(overrides)
    data["mailboxes"] = [box]
    return parse(data).mailboxes[0]


def run_collect(imap: FakeImap, **mailbox_overrides):
    box = make_mailbox(**mailbox_overrides)
    factory = FakeImapFactory({box.host: imap})
    return collect_messages(box, "pw", WINDOW, factory), factory


class CollectMessagesTests(unittest.TestCase):
    def test_filters_by_internaldate_window(self):
        imap = FakeImap(
            {
                "INBOX": [
                    FakeMessage(1, "21-Nov-2026 06:59:59 +0100", header_block(sender="before@example.com")),
                    FakeMessage(2, "21-Nov-2026 07:00:00 +0100", header_block(sender="first@example.com")),
                    FakeMessage(3, "21-Nov-2026 12:59:59 +0100", header_block(sender="last@example.com")),
                    FakeMessage(4, "21-Nov-2026 13:00:00 +0100", header_block(sender="after@example.com")),
                ]
            }
        )
        messages, _ = run_collect(imap)
        self.assertEqual([m.sender for m in messages], ["first@example.com", "last@example.com"])
        self.assertEqual(messages[0].received, utc(2026, 11, 21, 6))

    def test_opens_folders_read_only_and_never_stores_flags(self):
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", header_block())]})
        run_collect(imap)
        selects = [call for call in imap.calls if call[0] == "select"]
        self.assertEqual(selects, [("select", '"INBOX"', True)])
        self.assertNotIn("STORE", imap.commands())
        self.assertNotIn("COPY", imap.commands())
        self.assertNotIn("EXPUNGE", imap.commands())
        self.assertTrue(imap.logged_out)

    def test_fetches_headers_with_peek_and_without_subject_by_default(self):
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", header_block())]})
        messages, _ = run_collect(imap)
        fetch = next(call for call in imap.calls if call[:2] == ("uid", "FETCH"))
        query = fetch[3]
        self.assertIn("BODY.PEEK[HEADER.FIELDS (FROM TO CC DATE)]", query)
        self.assertNotIn("SUBJECT", query)
        self.assertNotIn("BODY[]", query)
        self.assertIsNone(messages[0].subject)

    def test_subject_included_only_when_enabled(self):
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", header_block(subject="Hello"))]})
        messages, _ = run_collect(imap, include_subject=True)
        fetch = next(call for call in imap.calls if call[:2] == ("uid", "FETCH"))
        self.assertIn("SUBJECT", fetch[3])
        self.assertEqual(messages[0].subject, "Hello")

    def test_search_uses_since_one_day_before_window_start(self):
        imap = FakeImap()
        run_collect(imap)
        search = next(call for call in imap.calls if call[:2] == ("uid", "SEARCH"))
        self.assertEqual(search[2:], (None, "SINCE", "20-Nov-2026"))

    def test_no_fetch_when_search_is_empty(self):
        imap = FakeImap({"INBOX": []})
        messages, _ = run_collect(imap)
        self.assertEqual(messages, [])
        self.assertNotIn("FETCH", imap.commands())

    def test_multiple_folders_sorted_by_received(self):
        imap = FakeImap(
            {
                "INBOX": [FakeMessage(1, "21-Nov-2026 10:00:00 +0000", header_block(sender="inbox@example.com"))],
                "Junk": [FakeMessage(7, "21-Nov-2026 07:00:00 +0000", header_block(sender="junk@example.com"))],
            }
        )
        messages, _ = run_collect(imap, folders=["INBOX", "Junk"])
        self.assertEqual([(m.folder, m.sender) for m in messages], [("Junk", "junk@example.com"), ("INBOX", "inbox@example.com")])

    def test_fetch_is_batched(self):
        from mailbox_watcher import imap as imap_module

        messages = [FakeMessage(uid, "21-Nov-2026 08:00:00 +0000", header_block()) for uid in range(1, 251)]
        imap = FakeImap({"INBOX": messages})
        result, _ = run_collect(imap)
        fetches = [call for call in imap.calls if call[:2] == ("uid", "FETCH")]
        self.assertEqual(len(fetches), -(-250 // imap_module.FETCH_BATCH_SIZE))
        self.assertEqual(len(result), 250)

    def test_response_with_internaldate_before_uid(self):
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", header_block())]}, uid_first=False)
        messages, _ = run_collect(imap)
        self.assertEqual(len(messages), 1)

    def test_cc_is_reported_with_to(self):
        imap = FakeImap(
            {"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", header_block(cc="alias@example.org"))]}
        )
        messages, _ = run_collect(imap)
        self.assertEqual(messages[0].recipients, "owner@example.org, alias@example.org")

    def test_encoded_headers_are_decoded(self):
        headers = header_block(sender="=?utf-8?q?J=C3=BCrgen?= <juergen@example.com>")
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", headers)]})
        messages, _ = run_collect(imap)
        self.assertEqual(messages[0].sender, "Jürgen <juergen@example.com>")

    def test_folded_headers_are_single_line(self):
        headers = b"From: Alice\r\n <alice@example.com>\r\nTo: owner@example.org\r\nDate: x\r\n\r\n"
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", headers)]})
        messages, _ = run_collect(imap)
        self.assertNotIn("\n", messages[0].sender)
        self.assertEqual(messages[0].sender, "Alice <alice@example.com>")

    def test_missing_headers_become_empty(self):
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", b"\r\n")]})
        messages, _ = run_collect(imap)
        self.assertEqual((messages[0].sender, messages[0].recipients, messages[0].date), ("", "", ""))

    def test_uses_configured_host_and_port(self):
        imap = FakeImap()
        _, factory = run_collect(imap, port=1993)
        self.assertEqual(factory.opened, [("imap.example.org", 1993)])


class FailureTests(unittest.TestCase):
    def assertCategory(self, imap_or_factory, category, **overrides):
        box = make_mailbox(**overrides)
        factory = imap_or_factory if isinstance(imap_or_factory, FakeImapFactory) else FakeImapFactory({box.host: imap_or_factory})
        with self.assertRaises(MailboxCheckError) as ctx:
            collect_messages(box, "pw", WINDOW, factory)
        self.assertEqual(ctx.exception.category, category)
        return ctx.exception

    def test_login_rejected(self):
        imap = FakeImap(login_ok=False)
        self.assertCategory(imap, "authentication failed")
        self.assertTrue(imap.logged_out)

    def test_login_exception_does_not_leak_server_text(self):
        error = self.assertCategory(FakeImap(login_raises=True), "authentication failed")
        self.assertNotIn("secret server text", str(error))
        self.assertIsNone(error.__cause__)

    def test_missing_folder(self):
        self.assertCategory(FakeImap({"INBOX": []}), "folder could not be opened", folders=["Archive"])

    def test_search_failure(self):
        self.assertCategory(FakeImap(fail_command="search"), "search failed")

    def test_fetch_failure(self):
        imap = FakeImap({"INBOX": [FakeMessage(1, "21-Nov-2026 08:00:00 +0000", header_block())]}, fail_command="fetch")
        self.assertCategory(imap, "fetch failed")

    def test_unparseable_internaldate(self):
        imap = FakeImap({"INBOX": [FakeMessage(1, "garbage", header_block())]})
        self.assertCategory(imap, "unexpected server response")

    def test_connection_errors_are_categorized(self):
        cases = [
            (ssl.SSLCertVerificationError("bad cert"), "TLS certificate verification failed"),
            (ssl.SSLError("tls"), "TLS error"),
            (socket.timeout("timeout"), "connection timed out"),
            (socket.gaierror("dns"), "host name could not be resolved"),
            (ConnectionRefusedError("refused"), "connection failed"),
            (imaplib.IMAP4.error("bad greeting"), "IMAP protocol error"),
        ]
        for error, category in cases:
            with self.subTest(category=category):
                self.assertCategory(FakeImapFactory(error=error), category)

    def test_errors_during_session_are_categorized(self):
        imap = FakeImap(raise_on_command=imaplib.IMAP4.abort("connection lost"))
        self.assertCategory(imap, "IMAP protocol error")
        self.assertTrue(imap.logged_out)

    def test_socket_error_during_session(self):
        self.assertCategory(FakeImap(raise_on_command=ConnectionResetError()), "connection failed")


class HelperTests(unittest.TestCase):
    def test_parse_internaldate_with_offsets(self):
        self.assertEqual(parse_internaldate(b'1 (INTERNALDATE "21-Nov-2026 07:12:00 +0100")'), utc(2026, 11, 21, 6, 12))
        self.assertEqual(parse_internaldate(b'1 (INTERNALDATE "21-Nov-2026 01:00:00 -0230")'), utc(2026, 11, 21, 3, 30))
        self.assertEqual(parse_internaldate(b'1 (INTERNALDATE " 1-Dec-2026 00:00:00 +0000")'), utc(2026, 12, 1))

    def test_parse_internaldate_invalid(self):
        self.assertIsNone(parse_internaldate(b"1 (UID 4)"))
        self.assertIsNone(parse_internaldate(b'1 (INTERNALDATE "31-Feb-2026 00:00:00 +0000")'))
        self.assertIsNone(parse_internaldate(b'1 (INTERNALDATE "01-Xyz-2026 00:00:00 +0000")'))

    def test_imap_date_is_locale_independent(self):
        self.assertEqual(imap_date(utc(2026, 3, 5)), "05-Mar-2026")
        self.assertEqual(imap_date(utc(2026, 12, 31, 23, 59)), "31-Dec-2026")

    def test_quote_mailbox_name(self):
        self.assertEqual(quote_mailbox_name("INBOX"), '"INBOX"')
        self.assertEqual(quote_mailbox_name('My "Box"'), '"My \\"Box\\""')
        self.assertEqual(quote_mailbox_name("a\\b"), '"a\\\\b"')

    def test_sanitize(self):
        self.assertEqual(sanitize("a\r\n\tb  c\x00d"), "a b c d")
        long_value = sanitize("x" * 500)
        self.assertEqual(len(long_value), 200)
        self.assertTrue(long_value.endswith("…"))


if __name__ == "__main__":
    unittest.main()
