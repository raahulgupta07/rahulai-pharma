"""
Deck distribution pipeline.

Stub-mode by default: if SMTP / Slack credentials are missing, calls log
the intended send + return mode='stub' instead of raising. If credentials
ARE set but actual delivery fails, errors propagate (fail-loud).
"""

from .email import send_email
from .slack import send_slack
from .pdf import render_deck_to_pdf
from .delivery import deliver_scheduled_deck

__all__ = [
    "send_email",
    "send_slack",
    "render_deck_to_pdf",
    "deliver_scheduled_deck",
]
