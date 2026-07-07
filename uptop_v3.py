#!/usr/bin/env python3
"""
Toqan UpTop V3 monitoring insights — Apache Airflow DAG + pipeline helpers.

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
    S3_FOLDER_BAJAJ,
    SUPPORTED_EXTENSIONS,
)
from config.prompts import get_prompt_by_model_name
from email_service.template import create_html_email, EMAIL_CONFIG
from email_service.sender import send_email
from utils.helpers import get_analysis, make_api_request, remove_emojis
from utils.uptop_v3_extractor import extract_from_html, build_label_whitelist
from utils.utils import TOQAN_API_KEY, TOQAN_BASE_URL
from email_service.error_notifier import dag_failure_callback, notify_error

MODEL_NAME = "uptop_v3"


# ============================================================================
# HELPER FUNCTIONS (used by both main() and Airflow tasks)
# ============================================================================

def find_latest_file():
    """
    Find latest file in S3 bucket.

    Returns:
        tuple: (s3_key, file_name, file_size)  # file_size in MB
    """
    print(f"\n📂 Finding latest file in s3://{S3_BUCKET}/{S3_FOLDER_BAJAJ}")
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_FOLDER_BAJAJ)

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


def extract_html_to_json(file_name, html_buffer):
    """
    Convert the downloaded UpTop V3 HTML report into structured raw-data JSON.

    Toqan analyzes this JSON instead of the raw HTML for this model — the
    extractor pulls every table into plain key/value data with no computed
    metrics, so the AI still does all calculation itself.

    Args:
        file_name:   Original HTML file name (used for date detection + naming).
        html_buffer: BytesIO buffer holding the downloaded HTML bytes.

    Returns:
        tuple: (json_file_name, json_buffer, label_whitelist)
            label_whitelist is a plain-text block of the exact category
            labels found in this file (score buckets, risk segments,
            approval methods, feature names) — injected into the LLM
            prompt so it cannot substitute generic industry-standard
            categories for this model's actual, non-standard labels.
    """
    print("\n[extract_html_to_json] START")
    html_buffer.seek(0)
    html_content = html_buffer.read().decode("utf-8")

    data = extract_from_html(html_content, source_label=file_name)
    label_whitelist = build_label_whitelist(data)

    # Compact (no indentation) — Toqan reads this as raw data, not for human
    # display, so pretty-printing only adds dead-weight tokens to the payload
    # the model has to ingest before it can even start writing the report.
    json_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    json_file_name = f"{Path(file_name).stem}.json"
    json_buffer = BytesIO(json_bytes)

    print(f"  [extract_html_to_json] source: {file_name!r} ({html_buffer.getbuffer().nbytes:,} bytes)")
    print(f"  [extract_html_to_json] output: {json_file_name!r} ({len(json_bytes):,} bytes)")
    print(f"  [extract_html_to_json] latest_month: {data['_meta'].get('latest_month')!r}")
    print(f"  [extract_html_to_json] label_whitelist:\n{label_whitelist}")
    print(f"[extract_html_to_json] END")
    return json_file_name, json_buffer, label_whitelist


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


def create_analysis_conversation(file_id, label_whitelist=None):
    """Create analysis conversation with Toqan; returns (conversation_id, request_id).

    label_whitelist: plain-text block of the exact category labels found in
    this run's file (see build_label_whitelist) — injected into the prompt's
    {{VALID_LABELS}} marker so the model can't fall back on generic
    industry-standard categories instead of this file's real labels.
    """
    print(f"\n[create_analysis_conversation] START — model={MODEL_NAME!r}")
    time.sleep(5)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    prompt = get_prompt_by_model_name(MODEL_NAME, prompt_vars={"VALID_LABELS": label_whitelist})

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

    return analysis


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

    buffer = None
    json_buffer = None
    try:
        s3_key, file_name, _file_size = find_latest_file()
        buffer = download_file(s3_key)
        json_file_name, json_buffer, label_whitelist = extract_html_to_json(file_name, buffer)
        file_id = upload_to_toqan(json_file_name, json_buffer)
        conversation_id, request_id = create_analysis_conversation(file_id, label_whitelist)
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
        if buffer:
            buffer.close()
        if json_buffer:
            json_buffer.close()


# ============================================================================
# AIRFLOW DAG DEFINITION (classic PythonOperator style)
# ============================================================================

try:
    from airflow import DAG  # type: ignore[import-untyped]
    from airflow.operators.python import PythonOperator  # type: ignore[import-untyped]
    from airflow.utils.dates import days_ago  # type: ignore[import-untyped]
    _AIRFLOW_AVAILABLE = True
except ImportError:  # local run without Airflow installed
    _AIRFLOW_AVAILABLE = False


if _AIRFLOW_AVAILABLE:

    # ── Task callables ────────────────────────────────────────────────────
    # Each callable pulls/pushes via XCom so tasks stay decoupled.

    def _task_find_latest(**context):
        s3_key, file_name, file_size = find_latest_file()
        context["ti"].xcom_push(key="s3_key",       value=s3_key)
        context["ti"].xcom_push(key="file_name",    value=file_name)
        context["ti"].xcom_push(key="file_size_mb", value=round(file_size, 4))

    def _task_upload_file(**context):
        ti        = context["ti"]
        s3_key    = ti.xcom_pull(task_ids="find_latest", key="s3_key")
        file_name = ti.xcom_pull(task_ids="find_latest", key="file_name")
        buffer      = None
        json_buffer = None
        try:
            buffer = download_file(s3_key)
            json_file_name, json_buffer, label_whitelist = extract_html_to_json(file_name, buffer)
            file_id = upload_to_toqan(json_file_name, json_buffer)
        finally:
            if buffer:
                buffer.close()
            if json_buffer:
                json_buffer.close()
        ti.xcom_push(key="file_id", value=file_id)
        ti.xcom_push(key="label_whitelist", value=label_whitelist)

    def _task_start_analysis(**context):
        ti      = context["ti"]
        file_id = ti.xcom_pull(task_ids="upload_file", key="file_id")
        label_whitelist = ti.xcom_pull(task_ids="upload_file", key="label_whitelist")
        conv_id, request_id = create_analysis_conversation(file_id, label_whitelist)
        ti.xcom_push(key="conversation_id", value=conv_id)
        ti.xcom_push(key="request_id", value=request_id)

    def _task_fetch_and_email(**context):
        ti          = context["ti"]
        conv_id     = ti.xcom_pull(task_ids="start_analysis", key="conversation_id")
        request_id  = ti.xcom_pull(task_ids="start_analysis", key="request_id")
        file_name   = ti.xcom_pull(task_ids="find_latest",    key="file_name")
        analysis    = wait_for_analysis(conv_id, request_id)
        send_report_email(file_name, analysis, MODEL_NAME)

    # ── DAG ───────────────────────────────────────────────────────────────

    DAG_DEFAULT_ARGS = {
        "owner": "data",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": dag_failure_callback,
    }

    with DAG(
        dag_id="uptop_v3_insights",
        default_args=DAG_DEFAULT_ARGS,
        description="S3 → Toqan analysis → email (UpTop V3 model monitoring)",
        schedule_interval="0 9 * * 0",  # Every Sunday at 09:00 AM
        start_date=days_ago(1),
        catchup=False,
        max_active_runs=1,
        tags=["toqan", "uptop-v3", "insights", "bajaj"],
    ) as dag:

        t1_find_latest = PythonOperator(
            task_id="find_latest",
            python_callable=_task_find_latest,
        )

        t2_upload_file = PythonOperator(
            task_id="upload_file",
            python_callable=_task_upload_file,
        )

        t3_start_analysis = PythonOperator(
            task_id="start_analysis",
            python_callable=_task_start_analysis,
        )

        t4_fetch_and_email = PythonOperator(
            task_id="fetch_analysis_and_send_email",
            python_callable=_task_fetch_and_email,
        )

        t1_find_latest >> t2_upload_file >> t3_start_analysis >> t4_fetch_and_email


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()
