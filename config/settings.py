"""
Non-secret configuration (S3, file rules, polling, mail identities).

API keys and SMTP credentials live in .env and are loaded in utils.utils
(import utils.utils from DAGs for those values).
"""
import os

# ============================================================================
# S3 CONFIGURATION
# ============================================================================
S3_BUCKET = os.environ.get("S3_BUCKET", "lake-prod-ds-projects")
S3_FOLDER = os.environ.get("S3_FOLDER", "CL_TC_reports/")

# ============================================================================
# EMAIL IDENTITIES (not loaded from .env)
# ============================================================================
SENDER_EMAIL = "airflow-eks-prod@paysense.in"
RECIPIENT_EMAIL = ["paras.verma@payufin.com", "prakhar.gupta@payufin.com"]

# ============================================================================
# FILE CONFIGURATION
# ============================================================================
SUPPORTED_EXTENSIONS = (".xlsx", ".xls", ".csv", ".xlsm", ".xlsb", ".html")
FILE_NAME_PATTERN = None  # Set regex pattern or None

# ============================================================================
# ANALYSIS CONFIGURATION
# ============================================================================
MIN_ANALYSIS_LENGTH = 1500
MAX_POLL_ATTEMPTS = 60
POLL_INTERVAL = 8
