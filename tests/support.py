"""Shared test helpers: import path setup, sample configuration and fakes."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mailbox_watcher.report import Notification  # noqa: E402
from mailbox_watcher.tem import SendError  # noqa: E402

PROJECT_ID = "11111111-2222-3333-4444-555555555555"

BASE_CONFIG: dict[str, Any] = {
    "sender": {"email": "watcher@example.net", "name": "Mailbox Watcher"},
    "interval_hours": 6,
    "mailboxes": [
        {
            "name": "owner-example",
            "host": "imap.example.org",
            "username": "owner@example.org",
            "password_env": "MAILBOX_OWNER_EXAMPLE_PASSWORD",
            "notify": ["ceo@example.com"],
        }
    ],
}


def config(**overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE_CONFIG)
    data.update(overrides)
    return data


def config_json(data: dict[str, Any] | None = None) -> str:
    return json.dumps(BASE_CONFIG if data is None else data)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def header_block(
    sender: str = "Alice <alice@example.com>",
    to: str = "owner@example.org",
    date: str = "Sat, 21 Nov 2026 07:12:00 +0100",
    subject: str | None = "Your recovery code is 123456",
    cc: str | None = None,
) -> bytes:
    lines = [f"From: {sender}", f"To: {to}"]
    if cc:
        lines.append(f"Cc: {cc}")
    lines.append(f"Date: {date}")
    if subject is not None:
        lines.append(f"Subject: {subject}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


class FakeMessage:
    def __init__(self, uid: int, internaldate: str, headers: bytes) -> None:
        self.uid = uid
        self.internaldate = internaldate
        self.headers = headers


class FakeImap:
    """In-memory stand-in for imaplib.IMAP4_SSL recording every command."""

    def __init__(
        self,
        folders: dict[str, list[FakeMessage]] | None = None,
        login_ok: bool = True,
        login_raises: bool = False,
        fail_command: str | None = None,
        raise_on_command: BaseException | None = None,
        uid_first: bool = True,
    ) -> None:
        self.folders = folders if folders is not None else {"INBOX": []}
        self.login_ok = login_ok
        self.login_raises = login_raises
        self.fail_command = fail_command
        self.raise_on_command = raise_on_command
        self.uid_first = uid_first
        self.calls: list[tuple[Any, ...]] = []
        self.selected: str | None = None
        self.logged_out = False

    # imaplib-compatible API -------------------------------------------------
    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", user, password))
        if self.login_raises:
            import imaplib

            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED secret server text")
        return ("OK", [b"Logged in"]) if self.login_ok else ("NO", [b"denied"])

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        if self.fail_command == "select":
            return ("NO", [b"no such folder"])
        name = mailbox.strip('"').replace('\\"', '"').replace("\\\\", "\\")
        if name not in self.folders:
            return ("NO", [b"no such folder"])
        self.selected = name
        return ("OK", [str(len(self.folders[name])).encode()])

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        self.calls.append(("uid", command, *args))
        if self.raise_on_command is not None:
            raise self.raise_on_command
        if self.fail_command == command.lower():
            return ("NO", [b"failed"])
        messages = self.folders[self.selected or "INBOX"]
        if command == "SEARCH":
            return ("OK", [" ".join(str(m.uid) for m in messages).encode()])
        if command == "FETCH":
            wanted = {int(u) for u in args[0].split(",")}
            data: list[Any] = []
            for message in messages:
                if message.uid not in wanted:
                    continue
                if self.uid_first:
                    meta = f'{message.uid} (UID {message.uid} INTERNALDATE "{message.internaldate}" BODY[HEADER.FIELDS (FROM TO)] {{{len(message.headers)}}}'
                else:
                    meta = f'{message.uid} (INTERNALDATE "{message.internaldate}" UID {message.uid} BODY[HEADER.FIELDS (FROM TO)] {{{len(message.headers)}}}'
                data.append((meta.encode(), message.headers))
                data.append(b")")
            return ("OK", data)
        raise AssertionError(f"unexpected command {command}")

    def logout(self) -> tuple[str, list[bytes]]:
        self.calls.append(("logout",))
        self.logged_out = True
        return ("BYE", [b""])

    # helpers ----------------------------------------------------------------
    def commands(self) -> list[str]:
        return [call[1] if call[0] == "uid" else call[0] for call in self.calls]


class FakeImapFactory:
    def __init__(self, connections: dict[str, FakeImap] | None = None, error: BaseException | None = None) -> None:
        self.connections = connections or {}
        self.error = error
        self.opened: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> FakeImap:
        self.opened.append((host, port))
        if self.error is not None:
            raise self.error
        return self.connections[host]


class RecordingSender:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.sent: list[Notification] = []
        self.fail_for = fail_for or set()

    def send(self, notification: Notification) -> None:
        if any(marker in notification.subject for marker in self.fail_for):
            raise SendError("TEM API returned HTTP 503")
        self.sent.append(notification)
