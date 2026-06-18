"""SendGrid email provider."""

import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

from src.email.base import EmailProvider

logger = logging.getLogger(__name__)


class SendGridProvider(EmailProvider):
    """SendGrid email provider."""

    def __init__(self, api_key: str, from_email: str):
        """Initialize SendGrid provider."""
        self.client = SendGridAPIClient(api_key)
        self.from_email = from_email

    async def send(self, to: str, subject: str, html: str, text: str) -> bool:
        """Send email via SendGrid."""
        try:
            mail = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to),
                subject=subject,
                plain_text_content=Content("text/plain", text),
                html_content=Content("text/html", html),
            )

            response = self.client.send(mail)

            if response.status_code in (200, 201, 202):
                logger.info(
                    "Email sent successfully",
                    extra={"to": to, "subject": subject, "provider": "sendgrid"},
                )
                return True
            else:
                logger.error(
                    f"SendGrid returned status {response.status_code}",
                    extra={"to": to, "subject": subject, "provider": "sendgrid"},
                )
                return False

        except Exception as e:
            logger.error(
                f"Failed to send email via SendGrid: {str(e)}",
                extra={"to": to, "subject": subject, "provider": "sendgrid"},
            )
            return False
