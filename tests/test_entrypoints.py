from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
from support import PROJECT_ID, config_json, utc

import handler
from mailbox_watcher import __main__ as cli
from mailbox_watcher.config import CONFIG_ENV
from mailbox_watcher.report import MailboxStatus
from mailbox_watcher.runner import RunResult
from mailbox_watcher.window import ReportWindow

WINDOW = ReportWindow(utc(2026, 11, 21, 6), utc(2026, 11, 21, 12))


class HandlerTests(unittest.TestCase):
    def call(self, result=None, error=None):
        with mock.patch.object(handler, "run_from_environment") as run:
            if error is not None:
                run.side_effect = error
            else:
                run.return_value = result
            with self.assertLogs("mailbox_watcher", level="INFO"):
                return handler.handle({}, {})

    def test_success(self):
        result = RunResult(WINDOW, [MailboxStatus("owner-example", True, 3)])
        response = self.call(result)
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertTrue(body["ok"])
        self.assertEqual(body["mailboxes"][0]["new_messages"], 3)

    def test_partial_failure_returns_500(self):
        result = RunResult(WINDOW, [MailboxStatus("owner-example", False, 0, "authentication failed")])
        self.assertEqual(self.call(result)["statusCode"], 500)

    def test_configuration_error(self):
        from mailbox_watcher.config import ConfigError

        response = self.call(error=ConfigError("bad"))
        self.assertEqual(response["statusCode"], 500)
        self.assertEqual(json.loads(response["body"]), {"ok": False, "error": "configuration error"})

    def test_unexpected_error_is_not_leaked(self):
        response = self.call(error=RuntimeError("password=hunter2"))
        self.assertEqual(response["statusCode"], 500)
        self.assertNotIn("hunter2", response["body"])


class CliTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=True), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def write_config(self, directory):
        path = Path(directory) / "config.json"
        path.write_text(config_json(), encoding="utf-8")
        return path

    def test_validate_file(self):
        with tempfile.TemporaryDirectory() as directory:
            code, out, _ = self.run_cli("--config", str(self.write_config(directory)), "--validate")
        self.assertEqual(code, 0)
        self.assertIn("configuration ok: 1 mailbox(es), interval 6h", out)

    def test_validate_environment(self):
        code, out, _ = self.run_cli("--validate", env={CONFIG_ENV: config_json()})
        self.assertEqual(code, 0)

    def test_invalid_config(self):
        code, _, err = self.run_cli("--validate", env={CONFIG_ENV: "{}"})
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_missing_file(self):
        code, _, _ = self.run_cli("--config", "/nonexistent/config.json", "--validate")
        self.assertEqual(code, 2)

    def test_dry_run_prints_instead_of_sending(self):
        captured = {}

        def fake_run(settings, environ, now, sender, imap_factory=None):
            captured["sender"] = sender
            captured["now"] = now
            return RunResult(WINDOW, [MailboxStatus("owner-example", True, 0)])

        with mock.patch.object(cli, "run", fake_run):
            code, _, err = self.run_cli("--dry-run", "--now", "2026-11-21T13:05:00+01:00", env={CONFIG_ENV: config_json()})
        self.assertEqual(code, 0)
        self.assertEqual(type(captured["sender"]).__name__, "PrintSender")
        self.assertEqual(captured["now"], utc(2026, 11, 21, 12, 5))
        self.assertIn('"ok": true', err)

    def test_now_requires_offset(self):
        code, _, err = self.run_cli("--dry-run", "--now", "2026-11-21T12:00:00", env={CONFIG_ENV: config_json()})
        self.assertEqual(code, 2)
        self.assertIn("UTC offset", err)

    def test_send_mode_requires_tem_configuration(self):
        code, _, err = self.run_cli(env={CONFIG_ENV: config_json()})
        self.assertEqual(code, 2)
        self.assertIn("TEM", err)

    def test_failed_run_returns_1(self):
        def fake_run(*args, **kwargs):
            return RunResult(WINDOW, [MailboxStatus("owner-example", False, 0, "connection failed")])

        env = {CONFIG_ENV: config_json(), "TEM_SECRET_KEY": "k", "TEM_PROJECT_ID": PROJECT_ID}
        with mock.patch.object(cli, "run", fake_run):
            code, _, _ = self.run_cli(env=env)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
