"""Scaleway Serverless Functions entry point (handler: ``handler.handle``).

The function is triggered by a CRON trigger. The request payload is ignored;
each invocation reports the last complete interval, so repeated invocations
within the same interval produce the same report.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mailbox_watcher.config import ConfigError
from mailbox_watcher.runner import run_from_environment

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("mailbox_watcher")


def handle(event: Any, context: Any) -> dict[str, Any]:
    try:
        result = run_from_environment()
    except (ConfigError, ValueError) as exc:
        # Configuration problems are operator errors; messages contain no secrets.
        LOGGER.error("configuration error: %s", exc)
        return _response(500, {"ok": False, "error": "configuration error"})
    except Exception:  # noqa: BLE001 - never leak details through the HTTP response
        LOGGER.exception("unexpected error")
        return _response(500, {"ok": False, "error": "unexpected error"})

    summary = result.to_public_dict()
    LOGGER.info("run finished: %s", json.dumps(summary))
    return _response(200 if result.ok else 500, summary)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
