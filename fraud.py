#!/usr/bin/env python3
"""
Toqan fraud monitoring insights — Apache Airflow DAG + pipeline helpers.

Deploy: put this file (or repo) on Airflow's DAG path / PYTHONPATH. Ensure
`.env` is visible to `utils.utils` (repo root as cwd or symlink .env).

Airflow is imported only when available so `python fraud.py` still works locally
without installing apache-airflow.
"""
import time
from datetime import datetime, timedelta
from io import BytesIO

import boto3

from config.settings import (
    RECIPIENT_EMAIL,
    S3_BUCKET,
    S3_FOLDER,
    SUPPORTED_EXTENSIONS,
)
from config.prompts import get_prompt_by_model_name
from email_service.template import create_html_email, EMAIL_CONFIG
from email_service.sender import send_email
from utils.helpers import get_analysis, make_api_request, remove_emojis
from utils.utils import TOQAN_API_KEY, TOQAN_BASE_URL
from email_service.error_notifier import dag_failure_callback, notify_error

MODEL_NAME = "fraud"


# ============================================================================
# HELPER FUNCTIONS (used by both main() and Airflow tasks)
# ============================================================================

def find_latest_file():
    """
    Find latest file in S3 bucket.

    Returns:
        tuple: (s3_key, file_name, file_size)  # file_size in MB
    """
    print(f"\n📂 Finding latest file in s3://{S3_BUCKET}/{S3_FOLDER}")
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_FOLDER)

    files = [
        obj
        for obj in response.get("Contents", [])
        if obj["Key"].endswith(SUPPORTED_EXTENSIONS)
    ]

    if not files:
        raise RuntimeError("No supported files found in S3")

    latest = max(files, key=lambda x: x["LastModified"])
    s3_key = latest["Key"]
    file_name = s3_key.split("/")[-1]
    file_size = latest["Size"] / 1024 / 1024

    print(f"✓ Found: {file_name} ({file_size:.2f} MB)")
    return s3_key, file_name, file_size


def download_file(s3_key):
    """Download file from S3 to memory buffer."""
    print("\n⬇️  Downloading file from S3...")
    s3 = boto3.client("s3")
    buffer = BytesIO()
    s3.download_fileobj(Bucket=S3_BUCKET, Key=s3_key, Fileobj=buffer)
    buffer.seek(0)
    print("✓ Download complete")
    return buffer


def upload_to_toqan(file_name, file_buffer):
    """Upload file to Toqan API; returns file_id."""
    print("\n☁️  Uploading to Toqan...")
    time.sleep(3)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    files_payload = {"file": (file_name, file_buffer, "application/octet-stream")}

    upload_response = make_api_request(
        "PUT", f"{TOQAN_BASE_URL}/upload_file", headers, files=files_payload
    )

    file_id = upload_response.json()["file_id"]
    print(f"✓ Upload complete (ID: {file_id})")
    return file_id


def create_analysis_conversation(file_id):
    """Create analysis conversation with Toqan; returns conversation_id."""
    print(f"\n🤖 Creating analysis conversation for {MODEL_NAME}...")
    time.sleep(5)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    prompt = get_prompt_by_model_name(MODEL_NAME)

    print(f"✓ Loaded prompt for {MODEL_NAME} (length: {len(prompt)} chars)")

    conversation_data = {
        "user_message": prompt,
        "private_user_files": [{"ID": file_id}],
    }

    conv_response = make_api_request(
        "POST",
        f"{TOQAN_BASE_URL}/create_conversation",
        headers,
        json_data=conversation_data,
    )

    conversation_id = conv_response.json()["conversation_id"]
    print(f"✓ Conversation created (ID: {conversation_id})")
    return conversation_id


def wait_for_analysis(conversation_id):
    """Wait for and retrieve analysis from Toqan."""
    print("\n⏳ Waiting for analysis (2-5 min)...")
    time.sleep(30)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    analysis = get_analysis(conversation_id, headers, TOQAN_BASE_URL)

    if not analysis:
        raise RuntimeError("No analysis received from Toqan")

    return analysis


def send_report_email(file_name, analysis, model_name="fraud"):
    """Generate and send email report."""
    print("\n📧 Creating and sending email...")

    html_email = create_html_email(file_name, analysis, model_name)
    text_email = remove_emojis(analysis)

    subject = (
        f"{EMAIL_CONFIG.get(model_name, EMAIL_CONFIG['default'])['title']} - "
        f"{file_name} - {datetime.now().strftime('%Y-%m-%d')}"
    )

    send_email(
        subject=subject,
        html_content=html_email,
        text_content=text_email,
        recipients=RECIPIENT_EMAIL,
    )
    print("✓ Email sent successfully")


# ============================================================================
# CLI / LOCAL EXECUTION (for testing without Airflow)
# ============================================================================

def main():
    """Run full pipeline in one process (CLI / single operator)."""
    print("=" * 80)
    print("🚀 TOQAN ANALYSIS PIPELINE - LOCAL EXECUTION")
    print("=" * 80)

    buffer = None
    try:
        s3_key, file_name, _file_size = find_latest_file()
        buffer = download_file(s3_key)
        file_id = upload_to_toqan(file_name, buffer)
        conversation_id = create_analysis_conversation(file_id)
        analysis = wait_for_analysis(conversation_id)
        send_report_email(file_name, analysis, MODEL_NAME)

        print("\n" + "=" * 80)
        print("✅ SUCCESS - Pipeline completed!")
        print("=" * 80)
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        notify_error(e, source="main() — local CLI run")
        raise
    finally:
        if buffer:
            buffer.close()


# ============================================================================
# AIRFLOW DAG DEFINITION (TaskFlow — 4 tasks; analysis not in XCom)
# ============================================================================

try:
    from airflow.decorators import dag, task  # type: ignore[import-untyped]
except ImportError:  # local run without Airflow installed
    dag = None
    task = None


if dag is not None:

    @dag(
        dag_id="toqan_fraud_insights",
        description="S3 → Toqan analysis → email (fraud monitoring insights)",
        schedule="@daily",  # Runs once per day at midnight
        start_date=datetime(2026, 6, 17),  # Updated to June 17, 2026
        catchup=False,
        max_active_runs=1,
        default_args={
            "owner": "data",
            "retries": 2,
            "retry_delay": timedelta(minutes=5),
            "on_failure_callback": dag_failure_callback,
        },
        tags=["toqan", "fraud", "insights"],
    )
    def toqan_fraud_insights():
        """
        Fraud analysis pipeline (4 tasks).

        ``analysis`` text is not passed through XCom (size limits); the final
        task loads it in-process then sends email.
        """

        @task(task_id="find_latest")
        def find_latest():
            """Find latest file in S3 bucket."""
            s3_key, file_name, file_size = find_latest_file()
            return {
                "s3_key": s3_key,
                "file_name": file_name,
                "file_size_mb": round(file_size, 4),
            }

        @task(task_id="upload_file")
        def upload_file(meta: dict) -> dict:
            """Download from S3 and upload to Toqan API."""
            buffer = None
            try:
                buffer = download_file(meta["s3_key"])
                file_id = upload_to_toqan(meta["file_name"], buffer)
                return {**meta, "file_id": file_id}
            finally:
                if buffer:
                    buffer.close()

        @task(task_id="start_analysis")
        def start_analysis(meta: dict) -> dict:
            """Create analysis conversation with Toqan."""
            conversation_id = create_analysis_conversation(meta["file_id"])
            return {**meta, "conversation_id": conversation_id}

        @task(task_id="fetch_analysis_and_send_email")
        def fetch_analysis_and_send_email(meta: dict) -> dict:
            """Retrieve Toqan analysis and send email (keeps large text off XCom)."""
            analysis = wait_for_analysis(meta["conversation_id"])
            send_report_email(meta["file_name"], analysis, MODEL_NAME)
            return {
                "status": "success",
                "file_name": meta["file_name"],
                "timestamp": datetime.now().isoformat(),
            }

        file_meta = find_latest()
        uploaded = upload_file(file_meta)
        conversation = start_analysis(uploaded)
        fetch_analysis_and_send_email(conversation)

    # Instantiate the DAG
    toqan_fraud_insights_dag = toqan_fraud_insights()


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()
