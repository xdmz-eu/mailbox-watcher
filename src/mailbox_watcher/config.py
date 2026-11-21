"""Configuration loading and validation.

The non-secret configuration is a single JSON document, usually provided in
the ``MAILBOX_WATCHER_CONFIG`` environment variable. Passwords are never part
of this document: each mailbox names the environment variable that holds its
IMAP password (a Scaleway secret environment variable in production).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

CONFIG_ENV = "MAILBOX_WATCHER_CONFIG"

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
# Deliberately strict: plain addr-spec without display name or comments.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_FOLDER_RE = re.compile(r"^[\x20-\x7e]{1,200}$")

_TOP_LEVEL_KEYS = {"sender", "interval_hours", "mailboxes", "heartbeat"}
_SENDER_KEYS = {"email", "name"}
_MAILBOX_KEYS = {
    "name",
    "host",
    "port",
    "username",
    "password_env",
    "folders",
    "notify",
    "include_subject",
}
_HEARTBEAT_KEYS = {"hour_utc", "notify"}

DEFAULT_PORT = 993
DEFAULT_FOLDERS = ("INBOX",)
DEFAULT_INTERVAL_HOURS = 6
DEFAULT_SENDER_NAME = "Mailbox Watcher"


class ConfigError(ValueError):
    """Raised when the configuration is missing or invalid."""


@dataclass(frozen=True)
class Sender:
    email: str
    name: str


@dataclass(frozen=True)
class Mailbox:
    name: str
    host: str
    port: int
    username: str
    password_env: str
    folders: tuple[str, ...]
    notify: tuple[str, ...]
    include_subject: bool


@dataclass(frozen=True)
class Heartbeat:
    hour_utc: int
    notify: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    sender: Sender
    interval_hours: int
    mailboxes: tuple[Mailbox, ...]
    heartbeat: Heartbeat | None


def load_from_environment(environ: Mapping[str, str]) -> Settings:
    """Load settings from the JSON document in ``MAILBOX_WATCHER_CONFIG``."""
    raw = environ.get(CONFIG_ENV)
    if raw is None or not raw.strip():
        raise ConfigError(f"{CONFIG_ENV} is not set")
    return parse_json(raw)


def parse_json(raw: str) -> Settings:
    """Parse and validate a JSON configuration document."""
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"configuration is not valid JSON (line {exc.lineno})") from None
    return parse(data)


def parse(data: Any) -> Settings:
    """Validate an already decoded configuration document."""
    obj = _require_object(data, "configuration")
    _reject_unknown_keys(obj, _TOP_LEVEL_KEYS, "configuration")

    sender = _parse_sender(obj.get("sender"))

    interval = obj.get("interval_hours", DEFAULT_INTERVAL_HOURS)
    if not _is_int(interval) or interval < 1 or interval > 24 or 24 % interval != 0:
        raise ConfigError("interval_hours must be an integer that divides 24 (1, 2, 3, 4, 6, 8, 12 or 24)")

    raw_mailboxes = obj.get("mailboxes")
    if not isinstance(raw_mailboxes, list) or not raw_mailboxes:
        raise ConfigError("mailboxes must be a non-empty list")
    mailboxes = tuple(_parse_mailbox(item, index) for index, item in enumerate(raw_mailboxes))

    names = [mailbox.name for mailbox in mailboxes]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"duplicate mailbox names: {', '.join(duplicates)}")

    heartbeat = _parse_heartbeat(obj.get("heartbeat"), interval)

    return Settings(
        sender=sender,
        interval_hours=interval,
        mailboxes=mailboxes,
        heartbeat=heartbeat,
    )


def _parse_sender(value: Any) -> Sender:
    obj = _require_object(value, "sender")
    _reject_unknown_keys(obj, _SENDER_KEYS, "sender")
    email = _require_email(obj.get("email"), "sender.email")
    name = obj.get("name", DEFAULT_SENDER_NAME)
    if not isinstance(name, str) or not name.strip() or len(name) > 100 or _has_control_chars(name):
        raise ConfigError("sender.name must be a non-empty single-line string of at most 100 characters")
    return Sender(email=email, name=name.strip())


def _parse_mailbox(value: Any, index: int) -> Mailbox:
    where = f"mailboxes[{index}]"
    obj = _require_object(value, where)
    _reject_unknown_keys(obj, _MAILBOX_KEYS, where)

    name = obj.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(f"{where}.name must be lowercase letters, digits and hyphens (max 64 characters)")
    where = f"mailbox '{name}'"

    host = obj.get("host")
    if not isinstance(host, str) or not _HOST_RE.match(host) or ".." in host:
        raise ConfigError(f"{where}: host must be a valid hostname")

    port = obj.get("port", DEFAULT_PORT)
    if not _is_int(port) or not 1 <= port <= 65535:
        raise ConfigError(f"{where}: port must be an integer between 1 and 65535")

    username = obj.get("username")
    if not isinstance(username, str) or not username or len(username) > 320 or _has_control_chars(username):
        raise ConfigError(f"{where}: username must be a non-empty single-line string")

    password_env = obj.get("password_env")
    if not isinstance(password_env, str) or not _ENV_RE.match(password_env):
        raise ConfigError(f"{where}: password_env must be an environment variable name like MAILBOX_OWNER_PASSWORD")
    if password_env == CONFIG_ENV:
        raise ConfigError(f"{where}: password_env must not be {CONFIG_ENV}")

    folders_value = obj.get("folders", list(DEFAULT_FOLDERS))
    if not isinstance(folders_value, list) or not folders_value:
        raise ConfigError(f"{where}: folders must be a non-empty list")
    folders: list[str] = []
    for folder in folders_value:
        if not isinstance(folder, str) or not _FOLDER_RE.match(folder):
            raise ConfigError(f"{where}: folder names must be printable ASCII (1-200 characters)")
        if folder in folders:
            raise ConfigError(f"{where}: duplicate folder '{folder}'")
        folders.append(folder)

    notify = _parse_recipients(obj.get("notify"), f"{where}: notify")
    if username.lower() in {address.lower() for address in notify}:
        raise ConfigError(f"{where}: a mailbox must not notify itself")

    include_subject = obj.get("include_subject", False)
    if not isinstance(include_subject, bool):
        raise ConfigError(f"{where}: include_subject must be true or false")

    return Mailbox(
        name=name,
        host=host,
        port=port,
        username=username,
        password_env=password_env,
        folders=tuple(folders),
        notify=notify,
        include_subject=include_subject,
    )


def _parse_heartbeat(value: Any, interval_hours: int) -> Heartbeat | None:
    if value is None:
        return None
    obj = _require_object(value, "heartbeat")
    _reject_unknown_keys(obj, _HEARTBEAT_KEYS, "heartbeat")
    hour = obj.get("hour_utc")
    if not _is_int(hour) or not 0 <= hour <= 23:
        raise ConfigError("heartbeat.hour_utc must be an integer between 0 and 23")
    if hour % interval_hours != 0:
        raise ConfigError(
            f"heartbeat.hour_utc must be a window boundary (a multiple of interval_hours={interval_hours})"
        )
    notify = _parse_recipients(obj.get("notify"), "heartbeat.notify")
    return Heartbeat(hour_utc=hour, notify=notify)


def _parse_recipients(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where} must be a non-empty list of email addresses")
    recipients: list[str] = []
    for item in value:
        address = _require_email(item, where)
        if address.lower() in {existing.lower() for existing in recipients}:
            raise ConfigError(f"{where}: duplicate recipient")
        recipients.append(address)
    return tuple(recipients)


def _require_email(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) > 254 or not _EMAIL_RE.match(value):
        raise ConfigError(f"{where} must be a plain email address")
    return value


def _require_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a JSON object")
    return value


def _reject_unknown_keys(obj: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown keys: {', '.join(unknown)}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
