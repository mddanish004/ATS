"""Minimal email-service boundary.

The PRD requires transactional email via Resend behind an ``EmailService``
abstraction. The full Resend integration (templates, delivery tracking,
retries/background workers) is out of scope for the current task; this module
provides the seam so authentication flows can emit email requests without
depending on a provider. A future ``ResendEmailProvider`` can implement the
same ``EmailService`` protocol.
"""

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class EmailService(Protocol):
    def send_verification_email(self, *, to_email: str, verification_token: str) -> None:
        """Request delivery of a verification email.

        Implementations own rendering/sending. Callers must not log the
        token; only the provider implementation may handle the raw value,
        and it must never write it to logs.
        """
        ...

    def send_password_reset_email(self, *, to_email: str, reset_token: str) -> None:
        """Request delivery of a password-reset email.

        Same secrecy contract as above. The email must contain only the
        reset link/token -- never the password or other account details.
        """
        ...


class LoggingEmailService:
    """Development placeholder: records the intent without the secret."""

    def send_verification_email(
        self, *, to_email: str, verification_token: str
    ) -> None:
        _ = verification_token  # intentionally never logged
        logger.info("verification email queued for %s", to_email)

    def send_password_reset_email(self, *, to_email: str, reset_token: str) -> None:
        _ = reset_token  # intentionally never logged
        logger.info("password reset email queued for %s", to_email)


email_service: EmailService = LoggingEmailService()
