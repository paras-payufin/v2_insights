from dotenv import load_dotenv
import os

load_dotenv()

# TOQAN API CONFIGURATION
TOQAN_API_KEY = os.getenv("TOQAN_API_KEY")
TOQAN_BASE_URL = os.getenv("TOQAN_BASE_URL")

# AWS SES SMTP CONFIGURATION
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "").split(",")
