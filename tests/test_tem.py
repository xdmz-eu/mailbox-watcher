from __future__ import annotations

import io
import json
import unittest
import urllib.error

import support  # noqa: F401
from support import PROJECT_ID

from mailbox_watcher.config import Sender
from mailbox_watcher.report import Notification
from mailbox_watcher.tem import PrintSender, SendError, TemSender

SENDER = Sender(email="watcher@example.net", name="Mailbox Watcher")
NOTIFICATION = Notification(recipients=("ceo@example.com", "it@example.com"), subject="Subject", text="Body\n")


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status


class RecordingOpener:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def make_sender(opener, **overrides):
    kwargs = dict(secret_key="scw-secret", project_id=PROJECT_ID, region="fr-par", sender=SENDER, opener=opener)
    kwargs.update(overrides)
    return TemSender(**kwargs)


class TemSenderTests(unittest.TestCase):
    def test_request_shape(self):
        opener = RecordingOpener()
        make_sender(opener).send(NOTIFICATION)
        request, timeout = opener.requests[0]
        self.assertEqual(
            request.full_url,
            "https://api.scaleway.com/transactional-email/v1alpha1/regions/fr-par/emails",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-auth-token"), "scw-secret")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(timeout, 20)
        body = json.loads(request.data)
        self.assertEqual(
            body,
            {
                "from": {"email": "watcher@example.net", "name": "Mailbox Watcher"},
                "to": [{"email": "ceo@example.com"}, {"email": "it@example.com"}],
                "subject": "Subject",
                "text": "Body\n",
                "project_id": PROJECT_ID,
            },
        )

    def test_other_region(self):
        sender = make_sender(RecordingOpener(), region="nl-ams")
        self.assertTrue(sender.url.endswith("/regions/nl-ams/emails"))

    def test_http_error(self):
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, io.BytesIO(b"secret detail"))
        with self.assertRaises(SendError) as ctx:
            make_sender(RecordingOpener(error=error)).send(NOTIFICATION)
        self.assertEqual(str(ctx.exception), "TEM API returned HTTP 401")

    def test_non_2xx_response(self):
        with self.assertRaises(SendError):
            make_sender(RecordingOpener(response=FakeResponse(302))).send(NOTIFICATION)

    def test_network_errors(self):
        for error in (urllib.error.URLError("down"), TimeoutError(), ConnectionResetError()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(SendError):
                    make_sender(RecordingOpener(error=error)).send(NOTIFICATION)

    def test_secret_not_in_error_messages(self):
        error = urllib.error.URLError("scw-secret")
        with self.assertRaises(SendError) as ctx:
            make_sender(RecordingOpener(error=error)).send(NOTIFICATION)
        self.assertNotIn("scw-secret", str(ctx.exception))

    def test_requires_recipients(self):
        with self.assertRaises(SendError):
            make_sender(RecordingOpener()).send(Notification((), "s", "t"))

    def test_constructor_validation(self):
        with self.assertRaises(ValueError):
            make_sender(RecordingOpener(), secret_key="")
        with self.assertRaises(ValueError):
            make_sender(RecordingOpener(), project_id="not-a-uuid")
        with self.assertRaises(ValueError):
            make_sender(RecordingOpener(), region="fr-par/../x")


class PrintSenderTests(unittest.TestCase):
    def test_prints_notification(self):
        stream = io.StringIO()
        PrintSender(stream).send(NOTIFICATION)
        output = stream.getvalue()
        self.assertIn("To:      ceo@example.com, it@example.com", output)
        self.assertIn("Subject: Subject", output)
        self.assertIn("Body", output)


if __name__ == "__main__":
    unittest.main()
