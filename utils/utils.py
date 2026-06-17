"""
Secrets from root .env — import this module from DAGs for API keys and SMTP auth.

Non-secret app config stays in config.settings.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Toqan
TOQAN_API_KEY = os.getenv("TOQAN_API_KEY")
TOQAN_BASE_URL = os.getenv("TOQAN_BASE_URL", "https://api.toqan.ai/api")

# AWS SES SMTP (credentials from .env; host/port have sensible defaults)
SMTP_SERVER = os.getenv("SMTP_SERVER", "email-smtp.ap-south-1.amazonaws.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
