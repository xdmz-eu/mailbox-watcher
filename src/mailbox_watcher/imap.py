"""Read-only IMAP access that collects header metadata for a report window.

Safety properties:

* Folders are opened with EXAMINE (``readonly=True``), so the server does not
  change any flags, including ``\\Seen``.
* Only selected header fields are fetched with ``BODY.PEEK``; message bodies
  are never downloaded.
* The Subject header is only fetched when the mailbox enables it.
"""

from __future__ import annotations

import imaplib
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.parser import BytesHeaderParser
from email.policy import default as default_policy
from typing import Any, Callable, Iterable, Protocol

from .config import Mailbox
from .window import ReportWindow

MAX_HEADER_LENGTH = 200
FETCH_BATCH_SIZE = 100
IMAP_TIMEOUT_SECONDS = 30

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_INDEX = {name: number for number, name in enumerate(_MONTHS, start=1)}
_INTERNALDATE_RE = re.compile(
    rb'INTERNALDATE "\s?(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})"'
)
_UID_RE = re.compile(rb"UID (\d+)")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


class ImapConnection(Protocol):
    """The subset of ``imaplib.IMAP4`` used by the watcher."""

    def login(self, user: str, password: str) -> Any: ...

    def select(self, mailbox: str = ..., readonly: bool = ...) -> Any: ...

    def uid(self, command: str, *args: Any) -> Any: ...

    def logout(self) -> Any: ...


ImapFactory = Callable[[str, int], ImapConnection]


class MailboxCheckError(Exception):
    """A mailbox could not be checked.

    ``category`` is a short, non-sensitive description that is safe to put
    into notification emails and logs. Server responses are not included.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class MessageSummary:
    folder: str
    received: datetime
    sender: str
    recipients: str
    date: str
    subject: str | None


def default_imap_factory(host: str, port: int) -> ImapConnection:
    """Open a TLS connection with certificate and hostname verification."""
    context = ssl.create_default_context()
    return imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=IMAP_TIMEOUT_SECONDS)


def collect_messages(
    mailbox: Mailbox,
    password: str,
    window: ReportWindow,
    imap_factory: ImapFactory = default_imap_factory,
) -> list[MessageSummary]:
    """Return metadata of all messages received in ``window``, oldest first."""
    try:
        connection = imap_factory(mailbox.host, mailbox.port)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailboxCheckError(_connection_category(exc)) from None

    try:
        try:
            _expect_ok(connection.login(mailbox.username, password), "authentication failed")
        except imaplib.IMAP4.error:
            raise MailboxCheckError("authentication failed") from None

        messages: list[MessageSummary] = []
        for folder in mailbox.folders:
            messages.extend(_collect_folder(connection, folder, window, mailbox.include_subject))
        messages.sort(key=lambda message: message.received)
        return messages
    except MailboxCheckError:
        raise
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailboxCheckError(_connection_category(exc)) from None
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001 - logout failures must not hide results
            pass


def _collect_folder(
    connection: ImapConnection,
    folder: str,
    window: ReportWindow,
    include_subject: bool,
) -> list[MessageSummary]:
    _expect_ok(connection.select(quote_mailbox_name(folder), readonly=True), "folder could not be opened")

    # SEARCH SINCE has day granularity and uses the server's notion of a day,
    # so search one extra day back and filter precisely by INTERNALDATE.
    since = imap_date(window.start - timedelta(days=1))
    typ, data = connection.uid("SEARCH", None, "SINCE", since)
    _expect_ok((typ, data), "search failed")
    uids = _parse_search(data)

    fields = "FROM TO CC DATE SUBJECT" if include_subject else "FROM TO CC DATE"
    query = f"(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS ({fields})])"

    results: list[MessageSummary] = []
    for batch in _batches(uids, FETCH_BATCH_SIZE):
        typ, data = connection.uid("FETCH", ",".join(batch), query)
        _expect_ok((typ, data), "fetch failed")
        for received, header_bytes in _parse_fetch(data):
            if window.contains(received):
                results.append(_summarize(folder, received, header_bytes, include_subject))
    return results


def _summarize(folder: str, received: datetime, header_bytes: bytes, include_subject: bool) -> MessageSummary:
    headers = BytesHeaderParser(policy=default_policy).parsebytes(header_bytes)
    recipients = ", ".join(part for part in (_header(headers, "To"), _header(headers, "Cc")) if part)
    return MessageSummary(
        folder=folder,
        received=received,
        sender=_header(headers, "From"),
        recipients=recipients,
        date=_header(headers, "Date"),
        subject=_header(headers, "Subject") if include_subject else None,
    )


def _header(headers: Any, name: str) -> str:
    try:
        value = headers.get(name)
    except Exception:  # noqa: BLE001 - malformed headers must not break a report
        return "(unparseable)"
    if value is None:
        return ""
    return sanitize(str(value))


def sanitize(value: str, limit: int = MAX_HEADER_LENGTH) -> str:
    """Collapse whitespace, strip control characters and truncate."""
    cleaned = _CONTROL_RE.sub(" ", value)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def quote_mailbox_name(name: str) -> str:
    """Quote an ASCII mailbox name as an IMAP quoted string."""
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def imap_date(moment: datetime) -> str:
    """Format a date for IMAP SEARCH without depending on the locale."""
    moment = moment.astimezone(timezone.utc)
    return f"{moment.day:02d}-{_MONTHS[moment.month - 1]}-{moment.year:04d}"


def parse_internaldate(response: bytes) -> datetime | None:
    """Extract INTERNALDATE from a FETCH response line as aware UTC datetime."""
    match = _INTERNALDATE_RE.search(response)
    if not match:
        return None
    day, month, year, hour, minute, second, sign, off_h, off_m = match.groups()
    month_number = _MONTH_INDEX.get(month.decode("ascii").capitalize())
    if month_number is None:
        return None
    offset = timedelta(hours=int(off_h), minutes=int(off_m))
    if sign == b"-":
        offset = -offset
    try:
        local = datetime(
            int(year), month_number, int(day), int(hour), int(minute), int(second),
            tzinfo=timezone(offset),
        )
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _parse_search(data: Iterable[Any]) -> list[str]:
    uids: list[str] = []
    for chunk in data:
        if not chunk:
            continue
        if isinstance(chunk, str):
            chunk = chunk.encode("ascii", "ignore")
        for token in chunk.split():
            if token.isdigit():
                uids.append(token.decode("ascii"))
    return uids


def _parse_fetch(data: Iterable[Any]) -> list[tuple[datetime, bytes]]:
    """Return (INTERNALDATE, header bytes) pairs from a UID FETCH response."""
    results: list[tuple[datetime, bytes]] = []
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, payload = item[0], item[1]
        if not isinstance(meta, bytes) or not isinstance(payload, bytes):
            continue
        if not _UID_RE.search(meta):
            continue
        received = parse_internaldate(meta)
        if received is None:
            raise MailboxCheckError("unexpected server response")
        results.append((received, payload))
    return results


def _batches(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _expect_ok(response: Any, category: str) -> None:
    if not isinstance(response, tuple) or not response or response[0] != "OK":
        raise MailboxCheckError(category)


def _connection_category(exc: BaseException) -> str:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed"
    if isinstance(exc, ssl.SSLError):
        return "TLS error"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "connection timed out"
    if isinstance(exc, socket.gaierror):
        return "host name could not be resolved"
    if isinstance(exc, OSError):
        return "connection failed"
    return "IMAP protocol error"
