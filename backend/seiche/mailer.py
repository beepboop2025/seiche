"""Email delivery via SMTP — stdlib only (smtplib + ssl), same no-dependency
ethos as the rest. Config from env so no secret is ever committed:

  SEICHE_SMTP_HOST   e.g. smtp.titan.email
  SEICHE_SMTP_PORT   465 (implicit TLS) default
  SEICHE_SMTP_USER   e.g. desk@seiche.info
  SEICHE_SMTP_PASS
  SEICHE_SMTP_FROM   defaults to SMTP_USER

send() is best-effort and fail-loud in logs but never raises into the pull
cycle — a mail outage must not stop the board from updating.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger("seiche.mailer")


def configured() -> bool:
    return bool(os.getenv("SEICHE_SMTP_HOST") and os.getenv("SEICHE_SMTP_USER")
                and os.getenv("SEICHE_SMTP_PASS"))


def send(to: str, subject: str, body: str) -> bool:
    if not configured():
        logger.info("mailer not configured (SEICHE_SMTP_*); skipping send to %s", to)
        return False
    host = os.getenv("SEICHE_SMTP_HOST")
    port = int(os.getenv("SEICHE_SMTP_PORT", "465"))
    user = os.getenv("SEICHE_SMTP_USER")
    pw = os.getenv("SEICHE_SMTP_PASS")
    sender = os.getenv("SEICHE_SMTP_FROM", user)

    msg = EmailMessage()
    msg["From"] = f"Seiche <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:  # 587 / STARTTLS
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(user, pw)
                s.send_message(msg)
        logger.info("sent mail to %s: %s", to, subject)
        return True
    except Exception as exc:  # never break the caller
        logger.warning("mail send to %s failed (host=%s port=%s): %s", to, host, port, exc)
        return False
