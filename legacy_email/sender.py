"""Send the digest email via SMTP. LEGACY — disabled by default (see config/settings.yaml email.enabled)."""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_digest(subject: str, html_body: str, plain_body: str, recipients: list) -> bool:
    """Send email to all recipients via BCC. Returns True on success, False on failure."""
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()

    if not all([host, user, password, recipients]):
        logger.error("SMTP not fully configured or no recipients")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = user
    msg["Bcc"] = ", ".join(recipients)

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    all_recipients = [user] + recipients

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, all_recipients, msg.as_string())
        logger.info("Email sent: '%s' to %d recipient(s)", subject, len(recipients))
        return True
    except smtplib.SMTPException as exc:
        logger.error("SMTP error: %s", exc)
        return False
    except OSError as exc:
        logger.error("Network error sending email: %s", exc)
        return False
