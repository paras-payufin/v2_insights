#!/usr/bin/env python3
"""
Toqan UpTop V3 monitoring insights — Apache Airflow DAG + pipeline helpers.

Flow:
  S3 HTML → extract raw tables → MMR calculate → attach JSON to Toqan → email HTML report

Deploy: put this file (or repo) on Airflow's DAG path / PYTHONPATH. Ensure
`.env` is visible to `utils.utils` (repo root as cwd or symlink .env).

Airflow is imported only when available so `python uptop_v3.py` still works
locally without installing apache-airflow.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import boto3

from config.settings import (
    RECIPIENT_EMAIL_UPTOP_V3,
    S3_BUCKET,
    S3_FOLDER_UPTOP,
    SUPPORTED_EXTENSIONS,
    UPTOP_V3_MMR_DATA_DIR,
)
from config.prompts import get_prompt_by_model_name
from email_service.template import create_html_email, EMAIL_CONFIG
from email_service.sender import send_email
from utils.helpers import extract_html_document, get_analysis, make_api_request, remove_emojis
from utils.utils import TOQAN_API_KEY, TOQAN_BASE_URL
from email_service.error_notifier import dag_failure_callback, notify_error
from uptop_v3_mmr.extractor import extract_raw_from_html
from uptop_v3_mmr.mmr_calculator import calculate_all

MODEL_NAME = "uptop_v3"


# ============================================================================
# HELPER FUNCTIONS (used by both main() and Airflow tasks)
# ============================================================================

def find_latest_file():
    """
    Find latest HTML/report file in S3 bucket.

    Returns:
        tuple: (s3_key, file_name, file_size)  # file_size in MB
    """
    print(f"\n📂 Finding latest file in s3://{S3_BUCKET}/{S3_FOLDER_UPTOP}")
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_FOLDER_UPTOP)

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


def build_mmr_json(html_buffer, source_name):
    """
    HTML buffer → raw table JSON → mmr_calculated.json payload.

    Returns:
        tuple: (json_bytes: BytesIO, json_file_name: str, mmr: dict)
    """
    print("\n🧮 Extracting HTML → raw JSON → MMR calculations...")
    html_bytes = html_buffer.getvalue() if hasattr(html_buffer, "getvalue") else html_buffer.read()
    if isinstance(html_bytes, BytesIO):
        html_bytes = html_bytes.getvalue()

    raw = extract_raw_from_html(html_bytes, source_name=source_name)
    mmr = calculate_all(raw)

    # Persist locally for debugging / audit (best-effort)
    try:
        data_dir = Path(UPTOP_V3_MMR_DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "extracted_data.json").write_text(
            json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (data_dir / "mmr_calculated.json").write_text(
            json.dumps(mmr, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  [mmr] wrote artifacts under {data_dir}")
    except OSError as exc:
        print(f"  [mmr] warning: could not write local artifacts: {exc}")

    json_bytes = json.dumps(mmr, indent=2, ensure_ascii=False).encode("utf-8")
    json_buffer = BytesIO(json_bytes)
    json_buffer.seek(0)

    stem = Path(source_name).stem if source_name else "uptop_v3"
    json_file_name = f"{stem}_mmr_calculated.json"

    print(
        f"✓ MMR ready — overall_rag={mmr.get('overall_rag')!r} "
        f"alerts={len(mmr.get('alerts') or [])} "
        f"size={len(json_bytes)} bytes"
    )
    return json_buffer, json_file_name, mmr


def upload_to_toqan(file_name, file_buffer, content_type="application/octet-stream"):
    """Upload file to Toqan API; returns file_id."""
    print("\n☁️  Uploading to Toqan...")
    time.sleep(3)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    files_payload = {"file": (file_name, file_buffer, content_type)}

    upload_response = make_api_request(
        "PUT", f"{TOQAN_BASE_URL}/upload_file", headers, files=files_payload
    )

    file_id = upload_response.json()["file_id"]
    print(f"✓ Upload complete (ID: {file_id})")
    return file_id


def create_analysis_conversation(file_id):
    """Create analysis conversation with Toqan; returns (conversation_id, request_id)."""
    print(f"\n[create_analysis_conversation] START — model={MODEL_NAME!r}")
    time.sleep(5)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    prompt = get_prompt_by_model_name(MODEL_NAME)

    print(f"  [create_analysis_conversation] prompt length : {len(prompt)} chars")
    print(f"  [create_analysis_conversation] prompt preview: {prompt[:100]!r}")
    print(f"  [attach] file_id being sent: {file_id!r}")

    conversation_data = {
        "user_message": prompt,
        "private_user_files": [{"id": file_id}],
    }
    print(f"  [attach] payload: {str(conversation_data)[:400]!r}")

    conv_response = make_api_request(
        "POST",
        f"{TOQAN_BASE_URL}/create_conversation",
        headers,
        json_data=conversation_data,
    )

    full_resp = conv_response.json()
    print(f"  [attach] create_conversation full response: {full_resp}")
    print(f"  [attach] conversation_id: {full_resp.get('conversation_id')!r}")

    resp_str = str(full_resp).lower()
    if file_id and file_id.lower() in resp_str:
        print(f"  [attach] ✓ file_id confirmed in response")
    else:
        print(f"  [attach] ⚠ WARNING — file_id NOT found in response. "
              f"File may not be attached. Check 'private_user_files' key format.")

    for key in ("files", "attached_files", "file_ids", "attachments", "private_user_files"):
        if key in full_resp:
            print(f"  [attach] response field '{key}': {full_resp[key]!r}")

    conversation_id = full_resp["conversation_id"]
    request_id = full_resp["request_id"]
    print(f"  [attach] request_id: {request_id!r}")
    print(f"[create_analysis_conversation] END")
    return conversation_id, request_id


def wait_for_analysis(conversation_id, request_id):
    """Wait for and retrieve analysis from Toqan."""
    print("\n⏳ Waiting for analysis (2-5 min)...")
    time.sleep(30)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    analysis = get_analysis(conversation_id, request_id, headers, TOQAN_BASE_URL)

    if not analysis:
        raise RuntimeError("No analysis received from Toqan")

    # Strip any leaked preamble/meta-commentary before the actual HTML document
    cleaned = extract_html_document(analysis)
    if cleaned != analysis:
        print(f"  [wait_for_analysis] Stripped leaked preamble/trailing text "
              f"({len(analysis) - len(cleaned)} chars removed)")
    return cleaned


def _is_html_output(text):
    """Return True when Toqan's output is a self-contained HTML document."""
    return text.strip()[:20].lower().lstrip().startswith(("<!doctype html", "<html"))


def send_report_email(file_name, analysis, model_name=MODEL_NAME):
    """Generate and send email report."""
    print("\n📧 Creating and sending email...")

    date_str = datetime.now().strftime('%Y-%m-%d')
    config = EMAIL_CONFIG.get(model_name, EMAIL_CONFIG["default"])
    subject = f"{config['title']} - {file_name} - {date_str}"

    if _is_html_output(analysis):
        text_body = (
            f"UpTop V3 Model Monitoring Report — {file_name} — {date_str}\n\n"
            f"Please view this email in an HTML-capable email client."
        )
        send_email(
            subject=subject,
            html_content=analysis,
            text_content=text_body,
            recipients=RECIPIENT_EMAIL_UPTOP_V3,
        )
        print("✓ HTML report sent as email body")
    else:
        html_email = create_html_email(file_name, analysis, model_name)
        text_email = remove_emojis(analysis)
        send_email(
            subject=subject,
            html_content=html_email,
            text_content=text_email,
            recipients=RECIPIENT_EMAIL_UPTOP_V3,
        )
        print("✓ Plain text report sent with template wrapper")


# ============================================================================
# CLI / LOCAL EXECUTION (for testing without Airflow)
# ============================================================================

def main():
    """Run full pipeline in one process (CLI / single operator)."""
    print("=" * 80)
    print("🚀 TOQAN UPTOP V3 PIPELINE - LOCAL EXECUTION")
    print("=" * 80)

    html_buffer = None
    json_buffer = None
    try:
        s3_key, file_name, _file_size = find_latest_file()
        html_buffer = download_file(s3_key)
        json_buffer, json_file_name, _mmr = build_mmr_json(html_buffer, file_name)
        file_id = upload_to_toqan(
            json_file_name, json_buffer, content_type="application/json"
        )
        conversation_id, request_id = create_analysis_conversation(file_id)
        analysis = wait_for_analysis(conversation_id, request_id)
        send_report_email(file_name, analysis, MODEL_NAME)

        print("\n" + "=" * 80)
        print("✅ SUCCESS - Pipeline completed!")
        print("=" * 80)
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        notify_error(e, source="main() — uptop_v3 local CLI run")
        raise
    finally:
        if html_buffer:
            html_buffer.close()
        if json_buffer:
            json_buffer.close()


# ============================================================================
# AIRFLOW DAG DEFINITION (TaskFlow API)
# ============================================================================

try:
    from airflow.decorators import dag, task  # type: ignore[import-untyped]
    from airflow.utils.dates import days_ago  # type: ignore[import-untyped]
    _AIRFLOW_AVAILABLE = True
except ImportError:  # local run without Airflow installed
    _AIRFLOW_AVAILABLE = False


if _AIRFLOW_AVAILABLE:

    @dag(
        dag_id="uptop_v3_insights",
        default_args={
            "owner": "data",
            "retries": 2,
            "retry_delay": timedelta(minutes=5),
            "on_failure_callback": dag_failure_callback,
        },
        description="S3 HTML → MMR JSON → Toqan HTML report → email (UpTop V3)",
        schedule_interval="0 9 * * 0",  # Every Sunday at 09:00 AM
        start_date=days_ago(1),
        catchup=False,
        max_active_runs=1,
        tags=["toqan", "uptop-v3", "insights", "bajaj"],
    )
    def uptop_v3_insights():
        """S3 HTML → extract/calculate MMR JSON → Toqan → email."""

        @task(task_id="find_latest")
        def find_latest():
            s3_key, file_name, file_size = find_latest_file()
            return {
                "s3_key": s3_key,
                "file_name": file_name,
                "file_size_mb": round(file_size, 4),
            }

        @task(task_id="extract_calculate_upload")
        def extract_calculate_upload(file_info: dict):
            html_buffer = None
            json_buffer = None
            try:
                html_buffer = download_file(file_info["s3_key"])
                json_buffer, json_file_name, mmr = build_mmr_json(
                    html_buffer, file_info["file_name"]
                )
                file_id = upload_to_toqan(
                    json_file_name, json_buffer, content_type="application/json"
                )
            finally:
                if html_buffer:
                    html_buffer.close()
                if json_buffer:
                    json_buffer.close()
            return {
                "file_id": file_id,
                "json_file_name": json_file_name,
                "overall_rag": mmr.get("overall_rag"),
            }

        @task(task_id="start_analysis")
        def start_analysis(upload_info: dict):
            conversation_id, request_id = create_analysis_conversation(
                upload_info["file_id"]
            )
            return {
                "conversation_id": conversation_id,
                "request_id": request_id,
            }

        @task(task_id="fetch_analysis_and_send_email")
        def fetch_analysis_and_send_email(file_info: dict, analysis_info: dict):
            analysis = wait_for_analysis(
                analysis_info["conversation_id"],
                analysis_info["request_id"],
            )
            send_report_email(file_info["file_name"], analysis, MODEL_NAME)

        file_info = find_latest()
        upload_info = extract_calculate_upload(file_info)
        analysis_info = start_analysis(upload_info)
        fetch_analysis_and_send_email(file_info, analysis_info)

    # Register DAG with Airflow
    dag = uptop_v3_insights()


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()
