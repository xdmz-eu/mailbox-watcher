"""Serverless IMAP watcher that reports new mail metadata to other mailboxes.

The watcher never forwards message bodies. Reports contain header metadata
only (From, To, Date and, if explicitly enabled per mailbox, Subject).
"""

__version__ = "0.1.0"
