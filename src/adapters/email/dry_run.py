from src.adapters.email.base import SenderAdapter
from src.logging import get_logger

log = get_logger("dry_run_sender")


class DryRunSender(SenderAdapter):
    def send(self, recipient: str, subject: str, body_html: str) -> bool:
        log.info(
            "dry_run_email",
            recipient=recipient,
            subject=subject,
        )
        return True