"""Outbound email, currently used for password-reset links.

Optional by design: with no ``LEGAL_AI_SMTP_HOST`` configured the mailer is
disabled and callers decide how to surface that (development logs the link).
Uses the stdlib ``smtplib`` only — no new dependency.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from backend.core.config import Settings

logger = logging.getLogger(__name__)


def _send_sync(settings: Settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_username
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def send_email(settings: Settings, to: str, subject: str, body: str) -> bool:
    """Send an email via SMTP. Returns False when disabled or on failure.

    Never raises: a mail outage must not break the requesting endpoint.
    """
    if not settings.smtp_host:
        logger.info("mailer disabled (LEGAL_AI_SMTP_HOST unset); email to %s not sent", to)
        return False
    try:
        await asyncio.to_thread(_send_sync, settings, to, subject, body)
        return True
    except Exception:
        logger.exception("failed to send email to %s", to)
        return False
