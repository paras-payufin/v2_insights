"""
Non-secret configuration (S3, file rules, polling, mail identities).

API keys and SMTP credentials live in .env and are loaded in utils.utils
(import utils.utils from DAGs for those values).
"""
import os

# ============================================================================
# S3 CONFIGURATION
# ============================================================================
# S3_BUCKET = os.environ.get("S3_BUCKET", "lake-prod-ds-projects")
# S3_FOLDER = os.environ.get("S3_FOLDER", "CL_TC_reports/")



# S3 Config — Sandbox bucket
S3_BUCKET        = os.environ.get("S3_BUCKET", "dsa-data-sbox")

# Set folder based on which file you're working with
S3_FOLDER_BAJAJ  = os.environ.get("S3_FOLDER", "bajaj_strategic/")
S3_FOLDER_FRAUD  = os.environ.get("S3_FOLDER", "fraud/")
S3_FOLDER_UPTOP  = os.environ.get("S3_FOLDER", "uptop_v3/")


# ============================================================================
# EMAIL IDENTITIES (not loaded from .env)
# ============================================================================
SENDER_EMAIL = "airflow-eks-prod@paysense.in"
RECIPIENT_EMAIL = ["paras.verma@payufin.com"]
RECIPIENT_EMAIL_UPTOP_V3 = [
    "paras.verma@payufin.com"
    # "saurav.sarkar@wibmo.com",
    # "sumit.yadav@payufin.com",
    # "abhishek.singh@payufin.com",
]

# ============================================================================
# FILE CONFIGURATION
# ============================================================================
SUPPORTED_EXTENSIONS = (".xlsx", ".xls", ".csv", ".xlsm", ".xlsb", ".html")
FILE_NAME_PATTERN = None  # Set regex pattern or None

# ============================================================================
# ANALYSIS CONFIGURATION
# ============================================================================
MIN_ANALYSIS_LENGTH = 1500          # plain-text reports (short summaries)
MIN_HTML_ANALYSIS_LENGTH = 30000    # HTML reports — Toqan generates 30-150 KB
MAX_POLL_ATTEMPTS = 120             # 120 × 10s = 1200s (~20 min) headroom
POLL_INTERVAL = 10
THINK_DONE_PATIENCE = 50            # consecutive think=done + 0 chars polls before escalation warning
