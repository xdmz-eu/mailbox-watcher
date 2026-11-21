"""Plain-text notification emails.

Reports contain only header metadata. Failure alerts contain only a short,
non-sensitive error category. Neither ever contains message bodies, IMAP
usernames of other mailboxes or secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .imap import MessageSummary
from .window import ReportWindow

SUBJECT_PREFIX = "[mailbox-watcher]"
MAX_LISTED_MESSAGES = 200

FOOTER = (
    "This notification lists header metadata only. Message contents are not forwarded.\n"
    "Sign in to the watched mailbox directly to read the messages."
)


@dataclass(frozen=True)
class Notification:
    recipients: tuple[str, ...]
    subject: str
    text: str


@dataclass(frozen=True)
class MailboxStatus:
    name: str
    ok: bool
    message_count: int
    error: str | None = None


def format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_window(window: ReportWindow) -> str:
    return f"{format_utc(window.start)} to {format_utc(window.end)}"


def build_report(
    mailbox_name: str,
    recipients: Sequence[str],
    window: ReportWindow,
    messages: Sequence[MessageSummary],
    multiple_folders: bool,
) -> Notification:
    count = len(messages)
    noun = "message" if count == 1 else "messages"
    lines = [
        f"Mailbox: {mailbox_name}",
        f"Window:  {format_window(window)}",
        f"New {noun}: {count}",
        "",
    ]
    for number, message in enumerate(messages[:MAX_LISTED_MESSAGES], start=1):
        lines.append(f"{number}. Received: {format_utc(message.received)}")
        if multiple_folders:
            lines.append(f"   Folder:   {message.folder}")
        lines.append(f"   From:     {message.sender or '(none)'}")
        lines.append(f"   To:       {message.recipients or '(none)'}")
        lines.append(f"   Date:     {message.date or '(none)'}")
        if message.subject is not None:
            lines.append(f"   Subject:  {message.subject or '(none)'}")
        lines.append("")
    if count > MAX_LISTED_MESSAGES:
        lines.append(f"... and {count - MAX_LISTED_MESSAGES} more.")
        lines.append("")
    lines.append(FOOTER)
    return Notification(
        recipients=tuple(recipients),
        subject=f"{SUBJECT_PREFIX} {count} new {noun} in {mailbox_name}",
        text="\n".join(lines) + "\n",
    )


def build_failure_alert(
    mailbox_name: str,
    recipients: Sequence[str],
    window: ReportWindow,
    category: str,
) -> Notification:
    text = "\n".join(
        [
            f"Mailbox: {mailbox_name}",
            f"Window:  {format_window(window)}",
            f"Problem: {category}",
            "",
            "The mailbox could not be checked. Messages received in this window",
            "will not be reported later. Check the mailbox directly and verify the",
            "watcher's credentials and the function logs.",
            "",
        ]
    )
    return Notification(
        recipients=tuple(recipients),
        subject=f"{SUBJECT_PREFIX} Check failed for {mailbox_name}",
        text=text,
    )


def build_heartbeat(
    recipients: Sequence[str],
    window: ReportWindow,
    statuses: Sequence[MailboxStatus],
) -> Notification:
    failed = [status for status in statuses if not status.ok]
    lines = [
        f"Window: {format_window(window)}",
        f"Mailboxes checked: {len(statuses)}, failed: {len(failed)}",
        "",
    ]
    for status in statuses:
        if status.ok:
            lines.append(f"- {status.name}: ok, {status.message_count} new")
        else:
            lines.append(f"- {status.name}: FAILED ({status.error})")
    lines.append("")
    lines.append("This daily heartbeat confirms that the watcher is running.")
    state = "all ok" if not failed else f"{len(failed)} failed"
    return Notification(
        recipients=tuple(recipients),
        subject=f"{SUBJECT_PREFIX} Heartbeat: {state}",
        text="\n".join(lines) + "\n",
    )
