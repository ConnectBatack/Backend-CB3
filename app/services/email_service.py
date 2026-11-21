import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import anyio

from app.core.config import get_settings

logger = logging.getLogger("batack.email")


@dataclass
class OutboxEmail:
    to: str
    subject: str
    body: str


class EmailService:
    """Service d'envoi d'e-mails (SMTP en production, journal/boîte en test/dev)."""

    def __init__(self) -> None:
        self.test_outbox: list[OutboxEmail] = []

    def clear_outbox(self) -> None:
        self.test_outbox.clear()

    def _send_smtp_sync(self, msg: EmailMessage) -> None:
        settings = get_settings()
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            if settings.SMTP_USER:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)

    async def send_email(self, *, to: str, subject: str, body: str) -> None:
        settings = get_settings()
        if settings.APP_ENV == "test":
            self.test_outbox.append(OutboxEmail(to=to, subject=subject, body=body))
            return

        if not settings.SMTP_HOST:
            logger.info("SMTP non configuré : e-mail pour %s (%s) non envoyé.", to, subject)
            return

        msg = EmailMessage()
        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        await anyio.to_thread.run_sync(self._send_smtp_sync, msg)


email_service = EmailService()


async def send_subscription_confirmation_email(
    *,
    to: str,
    project_slug: str,
    language: str,
    confirm_token: str,
    unsubscribe_token: str,
) -> None:
    settings = get_settings()
    base = settings.BACKEND_PUBLIC_URL.rstrip("/")
    prefix = settings.API_V1_PREFIX.rstrip("/")
    confirm_url = f"{base}{prefix}/subscriptions/confirm?token={confirm_token}"
    unsub_url = f"{base}{prefix}/subscriptions/unsubscribe?token={unsubscribe_token}"

    subject = f"Confirmez votre suivi du projet {project_slug}"
    body = (
        f"Bonjour,\n\n"
        f"Vous avez demandé à suivre les nouvelles du projet '{project_slug}' ({language}).\n"
        f"Confirmez votre inscription en cliquant sur ce lien :\n{confirm_url}\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez ce message ou désinscrivez-vous :\n"
        f"{unsub_url}\n"
    )
    await email_service.send_email(to=to, subject=subject, body=body)

