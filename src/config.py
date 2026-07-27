import os

from dotenv import load_dotenv

load_dotenv()

CLAIM_INTERVAL_SECONDS = 30
INVENTORY_POLL_INTERVAL = 3600 * 3

EMAIL_DRY_RUN = os.environ.get("EMAIL_DRY_RUN", "true").lower() == "true"
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM_ADDR = os.environ.get("SMTP_FROM_ADDR", "")