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

# Per-pipeline prefixes (independent env vars; S3_FOLDER kept as legacy override for Bajaj)
S3_FOLDER_BAJAJ  = os.environ.get("S3_FOLDER_BAJAJ", os.environ.get("S3_FOLDER", "bajaj_strategic/"))
S3_FOLDER_FRAUD  = os.environ.get("S3_FOLDER_FRAUD", "fraud/")
# UpTop V3 HTML reports currently land under bajaj_strategic/; override via S3_FOLDER_UPTOP
S3_FOLDER_UPTOP  = os.environ.get("S3_FOLDER_UPTOP", os.environ.get("S3_FOLDER", "bajaj_strategic/"))

# Local artifacts for HTML→JSON→MMR (gitignored)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPTOP_V3_MMR_DATA_DIR = os.environ.get(
    "UPTOP_V3_MMR_DATA_DIR",
    os.path.join(_REPO_ROOT, "data", "uptop-v3-mmr"),
)


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
MAX_POLL_ATTEMPTS = 120             # keep
POLL_INTERVAL = 10                  # keep
