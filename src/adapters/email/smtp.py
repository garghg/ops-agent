import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.adapters.email.base import SenderAdapter
from src.logging import get_logger

log = get_logger("smtp_sender")


class SMTPSender(SenderAdapter):
    def __init__(self, host: str, port: int, username: str, password: str, from_addr: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_addr = from_addr
    
    def send(self, recipient: str, subject: str, body_html: str) -> bool:
        try:
            msg = MIMEMultipart()
            msg["From"] = self.from_addr
            msg["To"] = recipient
            msg["Subject"] = subject
            msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(self.host, self.port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)

            log.info("email_sent", recipient=recipient, subject=subject)
            return True
        
        except Exception as e:  # noqa: BLE001
            log.error("email_failed", recipient=recipient, subject=subject, error=str(e))
            return False