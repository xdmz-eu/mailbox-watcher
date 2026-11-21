"""Email delivery through Scaleway Transactional Email (TEM).

Uses the TEM HTTP API with the standard library only:
``POST https://api.scaleway.com/transactional-email/v1alpha1/regions/{region}/emails``
authenticated with an ``X-Auth-Token`` header (an IAM secret key).
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol, TextIO

from .config import Sender
from .report import Notification

API_BASE = "https://api.scaleway.com/transactional-email/v1alpha1/regions"
REQUEST_TIMEOUT_SECONDS = 20

_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]{3}$")
_PROJECT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

Opener = Callable[..., Any]


class SendError(Exception):
    """A notification could not be delivered. The message is non-sensitive."""


class NotificationSender(Protocol):
    def send(self, notification: Notification) -> None: ...


class TemSender:
    def __init__(
        self,
        secret_key: str,
        project_id: str,
        region: str,
        sender: Sender,
        opener: Opener = urllib.request.urlopen,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not secret_key:
            raise ValueError("TEM secret key is empty")
        if not _PROJECT_RE.match(project_id):
            raise ValueError("TEM project ID must be a UUID")
        if not _REGION_RE.match(region):
            raise ValueError("TEM region must look like 'fr-par'")
        self._secret_key = secret_key
        self._project_id = project_id
        self._region = region
        self._sender = sender
        self._opener = opener
        self._timeout = timeout

    @property
    def url(self) -> str:
        return f"{API_BASE}/{self._region}/emails"

    def build_request(self, notification: Notification) -> urllib.request.Request:
        body = {
            "from": {"email": self._sender.email, "name": self._sender.name},
            "to": [{"email": address} for address in notification.recipients],
            "subject": notification.subject,
            "text": notification.text,
            "project_id": self._project_id,
        }
        return urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Auth-Token": self._secret_key,
            },
        )

    def send(self, notification: Notification) -> None:
        if not notification.recipients:
            raise SendError("notification has no recipients")
        request = self.build_request(notification)
        try:
            with self._opener(request, timeout=self._timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                if not 200 <= status < 300:
                    raise SendError(f"TEM API returned HTTP {status}")
        except urllib.error.HTTPError as exc:
            raise SendError(f"TEM API returned HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise SendError("TEM API could not be reached") from None
        except OSError:
            raise SendError("TEM API request failed") from None


class PrintSender:
    """Dry-run sender that writes notifications to a stream instead of sending."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def send(self, notification: Notification) -> None:
        write = self._stream.write
        write("=" * 72 + "\n")
        write(f"To:      {', '.join(notification.recipients)}\n")
        write(f"Subject: {notification.subject}\n\n")
        write(notification.text)
