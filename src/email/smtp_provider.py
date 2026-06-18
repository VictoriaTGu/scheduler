"""SMTP email provider."""

import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from aiosmtplib import SMTP

from src.email.base import EmailProvider

logger = logging.getLogger(__name__)


class SMTPProvider(EmailProvider):
    """SMTP email provider."""

    def __init__(self, host: str, port: int, user: str, password: str, from_email: str):
        """Initialize SMTP provider."""
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_email = from_email

    async def send(self, to: str, subject: str, html: str, text: str) -> bool:
        """Send email via SMTP."""
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = to

            # Attach text and HTML parts
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            # Send via SMTP
            async with SMTP(hostname=self.host, port=self.port) as smtp:
                await smtp.login(self.user, self.password)
                await smtp.send_message(msg)

            logger.info(
                "Email sent successfully",
                extra={"to": to, "subject": subject, "provider": "smtp"},
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to send email via SMTP: {str(e)}",
                extra={"to": to, "subject": subject, "provider": "smtp"},
            )
            return False
