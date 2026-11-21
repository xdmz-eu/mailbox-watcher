from __future__ import annotations

import json
import unittest

import support  # noqa: F401  (sets up the import path)
from support import BASE_CONFIG, config, config_json

from mailbox_watcher.config import (
    CONFIG_ENV,
    ConfigError,
    load_from_environment,
    parse,
    parse_json,
)


def mailbox(**overrides):
    data = dict(BASE_CONFIG["mailboxes"][0])
    data.update(overrides)
    return data


class ValidConfigTests(unittest.TestCase):
    def test_minimal_config_applies_defaults(self):
        settings = parse(config())
        self.assertEqual(settings.interval_hours, 6)
        self.assertIsNone(settings.heartbeat)
        self.assertEqual(settings.sender.email, "watcher@example.net")
        box = settings.mailboxes[0]
        self.assertEqual(box.port, 993)
        self.assertEqual(box.folders, ("INBOX",))
        self.assertEqual(box.notify, ("ceo@example.com",))
        self.assertFalse(box.include_subject)

    def test_default_sender_name(self):
        settings = parse(config(sender={"email": "watcher@example.net"}))
        self.assertEqual(settings.sender.name, "Mailbox Watcher")

    def test_multiple_mailboxes_and_recipients(self):
        data = config(
            mailboxes=[
                mailbox(),
                mailbox(
                    name="owner-second",
                    username="owner@example.com",
                    password_env="MAILBOX_SECOND_PASSWORD",
                    notify=["ceo@example.com", "it@example.com"],
                    folders=["INBOX", "Junk"],
                    include_subject=True,
                    port=1993,
                ),
            ]
        )
        settings = parse(data)
        self.assertEqual([m.name for m in settings.mailboxes], ["owner-example", "owner-second"])
        second = settings.mailboxes[1]
        self.assertEqual(second.notify, ("ceo@example.com", "it@example.com"))
        self.assertEqual(second.folders, ("INBOX", "Junk"))
        self.assertTrue(second.include_subject)
        self.assertEqual(second.port, 1993)

    def test_heartbeat(self):
        settings = parse(config(heartbeat={"hour_utc": 6, "notify": ["ceo@example.com"]}))
        self.assertEqual(settings.heartbeat.hour_utc, 6)
        self.assertEqual(settings.heartbeat.notify, ("ceo@example.com",))

    def test_all_valid_intervals(self):
        for interval in (1, 2, 3, 4, 6, 8, 12, 24):
            with self.subTest(interval=interval):
                self.assertEqual(parse(config(interval_hours=interval)).interval_hours, interval)

    def test_load_from_environment(self):
        settings = load_from_environment({CONFIG_ENV: config_json()})
        self.assertEqual(len(settings.mailboxes), 1)

    def test_example_config_file_is_valid(self):
        path = support.SRC.parent / "config.example.json"
        settings = parse_json(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(settings.mailboxes), 1)


class InvalidConfigTests(unittest.TestCase):
    def assertInvalid(self, data, fragment=None):
        with self.assertRaises(ConfigError) as ctx:
            parse(data)
        if fragment:
            self.assertIn(fragment, str(ctx.exception))

    def test_missing_environment_variable(self):
        with self.assertRaises(ConfigError):
            load_from_environment({})
        with self.assertRaises(ConfigError):
            load_from_environment({CONFIG_ENV: "  "})

    def test_invalid_json(self):
        with self.assertRaises(ConfigError):
            parse_json("{not json")

    def test_duplicate_json_keys(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_json('{"interval_hours": 6, "interval_hours": 12}')
        self.assertIn("duplicate JSON key", str(ctx.exception))

    def test_top_level_must_be_object(self):
        self.assertInvalid([])

    def test_unknown_keys_rejected_everywhere(self):
        self.assertInvalid(config(extra=1), "unknown keys")
        self.assertInvalid(config(sender={"email": "watcher@example.net", "reply_to": "x@example.net"}), "unknown keys")
        self.assertInvalid(config(mailboxes=[mailbox(password="secret")]), "unknown keys")
        self.assertInvalid(config(heartbeat={"hour_utc": 0, "notify": ["a@example.com"], "x": 1}), "unknown keys")

    def test_password_is_never_accepted_inline(self):
        self.assertInvalid(config(mailboxes=[mailbox(password="hunter2")]), "password")

    def test_invalid_intervals(self):
        for value in (0, 5, 7, 25, -6, "6", 6.0, True, None):
            with self.subTest(value=value):
                self.assertInvalid(config(interval_hours=value), "interval_hours")

    def test_sender_validation(self):
        self.assertInvalid(config(sender=None), "sender")
        self.assertInvalid(config(sender={"email": "not-an-address"}), "sender.email")
        self.assertInvalid(config(sender={"email": "Watcher <watcher@example.net>"}), "sender.email")
        self.assertInvalid(config(sender={"email": "watcher@example.net", "name": "a\r\nBcc: x@example.com"}))
        self.assertInvalid(config(sender={"email": "watcher@example.net", "name": ""}))

    def test_mailboxes_must_be_non_empty_list(self):
        self.assertInvalid(config(mailboxes=[]), "mailboxes")
        self.assertInvalid(config(mailboxes={}), "mailboxes")

    def test_mailbox_name(self):
        for name in ("", "Owner", "owner_1", "-owner", "owner-", "a" * 65, 5):
            with self.subTest(name=name):
                self.assertInvalid(config(mailboxes=[mailbox(name=name)]), "name")

    def test_duplicate_mailbox_names(self):
        second = mailbox(username="other@example.org", password_env="OTHER_PASSWORD")
        self.assertInvalid(config(mailboxes=[mailbox(), second]), "duplicate mailbox names")

    def test_host(self):
        for host in ("", "imap example.org", "imap..example.org", "-imap.example.org", None):
            with self.subTest(host=host):
                self.assertInvalid(config(mailboxes=[mailbox(host=host)]), "host")

    def test_port(self):
        for port in (0, 65536, "993", True, 99.5):
            with self.subTest(port=port):
                self.assertInvalid(config(mailboxes=[mailbox(port=port)]), "port")

    def test_username(self):
        for username in ("", None, "owner@example.org\r\n"):
            with self.subTest(username=username):
                self.assertInvalid(config(mailboxes=[mailbox(username=username)]), "username")

    def test_password_env(self):
        for name in ("", "lowercase", "1ABC", "WITH-DASH", CONFIG_ENV, None):
            with self.subTest(name=name):
                self.assertInvalid(config(mailboxes=[mailbox(password_env=name)]), "password_env")

    def test_folders(self):
        self.assertInvalid(config(mailboxes=[mailbox(folders=[])]), "folders")
        self.assertInvalid(config(mailboxes=[mailbox(folders="INBOX")]), "folders")
        self.assertInvalid(config(mailboxes=[mailbox(folders=["Entwürfe"])]), "ASCII")
        self.assertInvalid(config(mailboxes=[mailbox(folders=["INBOX", "INBOX"])]), "duplicate folder")
        self.assertInvalid(config(mailboxes=[mailbox(folders=["IN\nBOX"])]))

    def test_notify(self):
        self.assertInvalid(config(mailboxes=[mailbox(notify=[])]), "notify")
        self.assertInvalid(config(mailboxes=[mailbox(notify="ceo@example.com")]), "notify")
        self.assertInvalid(config(mailboxes=[mailbox(notify=["ceo@example.com\r\nBcc: x@example.com"])]))
        self.assertInvalid(config(mailboxes=[mailbox(notify=["ceo@example.com", "CEO@example.com"])]), "duplicate")

    def test_mailbox_must_not_notify_itself(self):
        self.assertInvalid(config(mailboxes=[mailbox(notify=["Owner@Example.org"])]), "notify itself")

    def test_include_subject_must_be_boolean(self):
        self.assertInvalid(config(mailboxes=[mailbox(include_subject="yes")]), "include_subject")

    def test_heartbeat_validation(self):
        self.assertInvalid(config(heartbeat={"hour_utc": 24, "notify": ["a@example.com"]}), "hour_utc")
        self.assertInvalid(config(heartbeat={"hour_utc": 7, "notify": ["a@example.com"]}), "boundary")
        self.assertInvalid(config(heartbeat={"hour_utc": 6, "notify": []}), "notify")
        self.assertInvalid(config(heartbeat="daily"), "heartbeat")

    def test_error_messages_do_not_contain_values(self):
        data = config(mailboxes=[mailbox(password_env="lower_secret_value")])
        with self.assertRaises(ConfigError) as ctx:
            parse(data)
        self.assertNotIn("lower_secret_value", str(ctx.exception))


class JsonRoundTripTests(unittest.TestCase):
    def test_parse_json_equals_parse(self):
        self.assertEqual(parse_json(json.dumps(config())), parse(config()))


if __name__ == "__main__":
    unittest.main()
