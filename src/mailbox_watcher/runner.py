"""One watcher run: check every mailbox and send the resulting notifications."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import Settings, load_from_environment
from .imap import ImapFactory, MailboxCheckError, collect_messages, default_imap_factory
from .report import (
    MailboxStatus,
    Notification,
    build_failure_alert,
    build_heartbeat,
    build_report,
)
from .tem import NotificationSender, SendError, TemSender
from .window import ReportWindow, report_window

LOGGER = logging.getLogger("mailbox_watcher")

TEM_SECRET_KEY_ENV = "TEM_SECRET_KEY"
TEM_PROJECT_ID_ENV = "TEM_PROJECT_ID"
TEM_REGION_ENV = "TEM_REGION"
DEFAULT_TEM_REGION = "fr-par"


@dataclass
class RunResult:
    window: ReportWindow
    statuses: list[MailboxStatus] = field(default_factory=list)
    send_failures: list[str] = field(default_factory=list)
    heartbeat_sent: bool = False

    @property
    def ok(self) -> bool:
        return all(status.ok for status in self.statuses) and not self.send_failures

    def to_public_dict(self) -> dict[str, Any]:
        """Summary without addresses or message metadata, safe for logs/HTTP."""
        return {
            "ok": self.ok,
            "window": {"start": self.window.start.isoformat(), "end": self.window.end.isoformat()},
            "mailboxes": [
                {
                    "name": status.name,
                    "ok": status.ok,
                    "new_messages": status.message_count,
                    "error": status.error,
                }
                for status in self.statuses
            ],
            "send_failures": list(self.send_failures),
            "heartbeat_sent": self.heartbeat_sent,
        }


def run(
    settings: Settings,
    environ: Mapping[str, str],
    now: datetime,
    sender: NotificationSender,
    imap_factory: ImapFactory = default_imap_factory,
) -> RunResult:
    window = report_window(now, settings.interval_hours)
    result = RunResult(window=window)

    for mailbox in settings.mailboxes:
        password = environ.get(mailbox.password_env)
        try:
            if not password:
                raise MailboxCheckError("password is not configured")
            messages = collect_messages(mailbox, password, window, imap_factory)
        except MailboxCheckError as exc:
            LOGGER.warning("mailbox %s: check failed: %s", mailbox.name, exc.category)
            result.statuses.append(MailboxStatus(mailbox.name, ok=False, message_count=0, error=exc.category))
            alert = build_failure_alert(mailbox.name, mailbox.notify, window, exc.category)
            _deliver(sender, alert, f"{mailbox.name}:alert", result)
            continue

        LOGGER.info("mailbox %s: %d new message(s)", mailbox.name, len(messages))
        result.statuses.append(MailboxStatus(mailbox.name, ok=True, message_count=len(messages)))
        if messages:
            report = build_report(
                mailbox.name,
                mailbox.notify,
                window,
                messages,
                multiple_folders=len(mailbox.folders) > 1,
            )
            _deliver(sender, report, f"{mailbox.name}:report", result)

    heartbeat = settings.heartbeat
    if heartbeat is not None and window.end.hour == heartbeat.hour_utc:
        notification = build_heartbeat(heartbeat.notify, window, result.statuses)
        result.heartbeat_sent = _deliver(sender, notification, "heartbeat", result)

    return result


def _deliver(sender: NotificationSender, notification: Notification, label: str, result: RunResult) -> bool:
    try:
        sender.send(notification)
    except SendError as exc:
        LOGGER.error("%s: delivery failed: %s", label, exc)
        result.send_failures.append(label)
        return False
    return True


def tem_sender_from_environment(environ: Mapping[str, str], settings: Settings) -> TemSender:
    secret_key = environ.get(TEM_SECRET_KEY_ENV, "")
    project_id = environ.get(TEM_PROJECT_ID_ENV, "")
    region = environ.get(TEM_REGION_ENV, "") or DEFAULT_TEM_REGION
    return TemSender(secret_key=secret_key, project_id=project_id, region=region, sender=settings.sender)


def run_from_environment(
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
    sender: NotificationSender | None = None,
    imap_factory: ImapFactory = default_imap_factory,
) -> RunResult:
    environ = os.environ if environ is None else environ
    settings = load_from_environment(environ)
    if sender is None:
        sender = tem_sender_from_environment(environ, settings)
    return run(
        settings,
        environ,
        now or datetime.now(timezone.utc),
        sender,
        imap_factory,
    )
