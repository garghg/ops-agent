from src.adapters.email.base import SenderAdapter
from src.adapters.email.dry_run import DryRunSender
from src.adapters.email.smtp import SMTPSender
from src.config import (
    EMAIL_DRY_RUN,
    SMTP_FROM_ADDR,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
)


def get_sender() -> SenderAdapter:
    if EMAIL_DRY_RUN:
        return DryRunSender()
    return SMTPSender(
        host=SMTP_HOST,
        port=SMTP_PORT,
        username=SMTP_USERNAME,
        password=SMTP_PASSWORD,
        from_addr=SMTP_FROM_ADDR,
    )