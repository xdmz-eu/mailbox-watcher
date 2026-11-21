"""Local command line entry point.

Examples::

    # Validate a configuration file
    python -m mailbox_watcher --config config.json --validate

    # Check real mailboxes and print the notifications instead of sending them.
    # Passwords are read from the environment variables named in the config.
    python -m mailbox_watcher --config config.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .config import CONFIG_ENV, ConfigError, parse_json
from .runner import run, tem_sender_from_environment
from .tem import PrintSender


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mailbox_watcher")
    parser.add_argument("--config", type=Path, help=f"JSON configuration file (default: ${CONFIG_ENV})")
    parser.add_argument("--validate", action="store_true", help="validate the configuration and exit")
    parser.add_argument("--dry-run", action="store_true", help="print notifications instead of sending them")
    parser.add_argument("--now", help="override the current time (ISO 8601 with offset), for testing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    try:
        raw = args.config.read_text(encoding="utf-8") if args.config else os.environ.get(CONFIG_ENV, "")
        settings = parse_json(raw)
    except (OSError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.validate:
        print(f"configuration ok: {len(settings.mailboxes)} mailbox(es), interval {settings.interval_hours}h")
        return 0

    now = datetime.now(timezone.utc)
    if args.now:
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError:
            print("error: --now must be ISO 8601", file=sys.stderr)
            return 2
        if now.tzinfo is None:
            print("error: --now must include a UTC offset", file=sys.stderr)
            return 2

    try:
        sender = PrintSender() if args.dry_run else tem_sender_from_environment(os.environ, settings)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = run(settings, os.environ, now, sender)
    print(json.dumps(result.to_public_dict(), indent=2), file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
