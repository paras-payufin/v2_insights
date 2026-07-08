#!/usr/bin/env python3
"""
Toqan fraud monitoring insights — Apache Airflow DAG + pipeline helpers.

Deploy: put this file (or repo) on Airflow's DAG path / PYTHONPATH. Ensure
`.env` is visible to `utils.utils` (repo root as cwd or symlink .env).

Airflow is imported only when available so `python fraud.py` still works locally
without installing apache-airflow.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time
from datetime import datetime, timedelta
from io import BytesIO

import boto3

from config.settings import (
    RECIPIENT_EMAIL,
    S3_BUCKET,
    S3_FOLDER_FRAUD,
    SUPPORTED_EXTENSIONS,
)
from config.prompts import get_prompt_by_model_name
from email_service.template import create_html_email, EMAIL_CONFIG
from email_service.sender import send_email
from utils.helpers import extract_html_document, get_analysis, make_api_request, remove_emojis
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
    print(f"\n[find_latest_file] START — s3://{S3_BUCKET}/{S3_FOLDER_FRAUD}")
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_FOLDER_FRAUD)

    all_objects = response.get("Contents", [])
    files = [obj for obj in all_objects if obj["Key"].endswith(SUPPORTED_EXTENSIONS)]

    print(f"  [find_latest_file] total objects in prefix : {len(all_objects)}")
    print(f"  [find_latest_file] files matching extensions: {len(files)}")

    if not files:
        raise RuntimeError("No supported files found in S3")

    sorted_files = sorted(files, key=lambda x: x["LastModified"], reverse=True)
    print("  [find_latest_file] top 3 most recent files:")
    for obj in sorted_files[:3]:
        print(f"    • {obj['Key']}  (modified: {obj['LastModified']})")

    latest = sorted_files[0]
    s3_key = latest["Key"]
    file_name = s3_key.split("/")[-1]
    file_size = latest["Size"] / 1024 / 1024

    print(f"  [find_latest_file] selected : {s3_key}")
    print(f"  [find_latest_file] name     : {file_name}")
    print(f"  [find_latest_file] size     : {file_size:.2f} MB")
    print(f"  [find_latest_file] modified : {latest['LastModified']}")
    print(f"[find_latest_file] END")
    return s3_key, file_name, file_size


def download_file(s3_key):
    """Download file from S3 to memory buffer."""
    print(f"\n[download_file] START — bucket={S3_BUCKET!r} key={s3_key!r}")
    s3 = boto3.client("s3")
    buffer = BytesIO()
    t0 = time.time()
    s3.download_fileobj(Bucket=S3_BUCKET, Key=s3_key, Fileobj=buffer)
    elapsed = time.time() - t0
    buffer.seek(0)
    size_bytes = buffer.getbuffer().nbytes
    print(f"  [download_file] downloaded {size_bytes:,} bytes in {elapsed:.1f}s")
    print(f"[download_file] END")
    return buffer


def upload_to_toqan(file_name, file_buffer):
    """Upload file to Toqan API; returns file_id."""
    size_bytes = file_buffer.getbuffer().nbytes
    print(f"\n[upload_to_toqan] START — file={file_name!r} size={size_bytes:,} bytes")
    time.sleep(3)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    files_payload = {"file": (file_name, file_buffer, "application/octet-stream")}

    upload_response = make_api_request(
        "PUT", f"{TOQAN_BASE_URL}/upload_file", headers, files=files_payload
    )

    full_upload_resp = upload_response.json()
    print(f"  [upload_to_toqan] full API response: {full_upload_resp}")

    file_id = full_upload_resp.get("file_id")
    if not file_id:
        print(f"  [upload_to_toqan] WARNING — file_id missing from response! keys={list(full_upload_resp.keys())}")
    else:
        print(f"  [upload_to_toqan] file_id: {file_id!r}")

    print(f"[upload_to_toqan] END")
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

    # Use lowercase "id" key — matches the Toqan API convention observed from responses
    conversation_data = {
        "user_message": prompt,
        "private_user_files": [{"id": file_id}],
    }
    print(f"  [attach] key used: 'id' (lowercase)")
    print(f"  [attach] payload (truncated): {str(conversation_data)[:400]!r}")

    conv_response = make_api_request(
        "POST",
        f"{TOQAN_BASE_URL}/create_conversation",
        headers,
        json_data=conversation_data,
    )

    full_resp = conv_response.json()
    print(f"  [attach] full create_conversation response: {full_resp}")
    print(f"  [attach] file_id sent: {file_id!r}")

    # Check whether the API acknowledged the file
    resp_str = str(full_resp).lower()
    if file_id and file_id.lower() in resp_str:
        print(f"  [attach] ✓ file_id confirmed in response")
    else:
        print(f"  [attach] ⚠ WARNING — file_id NOT found in response. "
              f"File may not be attached. Check 'private_user_files' key format.")

    # Surface any file-acknowledgement fields
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
    # (no-op if the answer isn't an HTML document).
    cleaned = extract_html_document(analysis)
    if cleaned != analysis:
        print(f"  [wait_for_analysis] Stripped leaked preamble/trailing text "
              f"({len(analysis) - len(cleaned)} chars removed)")
    return cleaned


def _is_html_output(text):
    """Return True when Toqan's output is a self-contained HTML document."""
    return text.strip()[:20].lower().lstrip().startswith(("<!doctype html", "<html"))


def _build_wrapper_email_body(file_name, date_str, model_name):
    """Plain HTML wrapper email body used when the full report is attached."""
    config = EMAIL_CONFIG.get(model_name, EMAIL_CONFIG["default"])
    primary = config.get("primary_color", "#0d2137")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f5f7fa;margin:0;padding:32px;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">
    <div style="background:{primary};padding:32px 40px;">
      <h1 style="color:#fff;margin:0;font-size:22px;">{config['title']}</h1>
      <p style="color:rgba(255,255,255,.8);margin:8px 0 0;font-size:14px;">{config.get('subtitle','')}</p>
    </div>
    <div style="padding:32px 40px;">
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Please find the <strong>Fraud Monitoring Report</strong> attached to this email.<br>
        Source file: <code>{file_name}</code> &nbsp;|&nbsp; Generated: {date_str}
      </p>
      <p style="color:#6b7280;font-size:13px;">
        Open the attached <code>.html</code> file in any web browser to view the full interactive report
        including charts, merchant deep-dives, PSI/CSI analysis, and early warning alerts.
      </p>
    </div>
    <div style="background:#f9fafb;padding:20px 40px;border-top:1px solid #e5e7eb;">
      <p style="color:#9ca3af;font-size:12px;margin:0;">{config.get('footer','Generated by Toqan Analysis Platform')}</p>
    </div>
  </div>
</body>
</html>"""


def send_report_email(file_name, analysis, model_name="fraud"):
    """Generate and send email report.

    HTML path  — Toqan's HTML is sent directly as the email body.
    Plain text — analysis is wrapped with create_html_email template.
    """
    print("\n[send_report_email] START")

    date_str = datetime.now().strftime('%Y-%m-%d')
    config = EMAIL_CONFIG.get(model_name, EMAIL_CONFIG["default"])
    subject = f"{config['title']} - {file_name} - {date_str}"
    output_type = "HTML" if _is_html_output(analysis) else "plain text"

    print(f"  [send_report_email] subject      : {subject!r}")
    print(f"  [send_report_email] recipients   : {RECIPIENT_EMAIL}")
    print(f"  [send_report_email] output type  : {output_type}")
    print(f"  [send_report_email] content size : {len(analysis):,} chars")

    if _is_html_output(analysis):
        print("  [send_report_email] path: HTML direct (no attachment)")
        send_email(
            subject=subject,
            html_content=analysis,
            text_content=(
                f"Fraud Monitoring Report — {file_name} — {date_str}\n\n"
                f"Please view this email in an HTML-capable email client."
            ),
            recipients=RECIPIENT_EMAIL,
        )
        print("✓ HTML report sent as email body")
    else:
        print("  [send_report_email] path: plain text → template wrapper")
        html_email = create_html_email(file_name, analysis, model_name)
        text_email = remove_emojis(analysis)
        send_email(
            subject=subject,
            html_content=html_email,
            text_content=text_email,
            recipients=RECIPIENT_EMAIL,
        )

    print(f"  [send_report_email] sent to: {', '.join(RECIPIENT_EMAIL) if isinstance(RECIPIENT_EMAIL, list) else RECIPIENT_EMAIL}")
    print(f"[send_report_email] END")


# ============================================================================
# CLI / LOCAL EXECUTION (for testing without Airflow)
# ============================================================================

def main():
    """Run full pipeline in one process (CLI / single operator)."""
    print("=" * 80)
    print("🚀 TOQAN ANALYSIS PIPELINE - LOCAL EXECUTION")
    print("=" * 80)

    buffer = None
    file_size = 0.0
    file_id = None
    conversation_id = None
    analysis = None
    try:
        s3_key, file_name, file_size = find_latest_file()
        buffer = download_file(s3_key)
        file_id = upload_to_toqan(file_name, buffer)
        conversation_id, request_id = create_analysis_conversation(file_id)
        analysis = wait_for_analysis(conversation_id, request_id)
        send_report_email(file_name, analysis, MODEL_NAME)

        print("\n" + "=" * 60)
        print("PIPELINE SUMMARY")
        print("=" * 60)
        print(f"  File         : {file_name}")
        print(f"  File size    : {file_size:.2f} MB")
        print(f"  File ID      : {file_id}")
        print(f"  Conversation : {conversation_id}")
        print(f"  Analysis len : {len(analysis):,} chars")
        print(f"  Output type  : {'HTML' if _is_html_output(analysis) else 'Plain text'}")
        print(f"  Recipients   : {RECIPIENT_EMAIL}")
        print(f"  Status       : SUCCESS")
        print("=" * 60 + "\n")
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        notify_error(e, source="main() — local CLI run")
        raise
    finally:
        if buffer:
            buffer.close()


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
        run_id = context.get("run_id", "unknown")
        print(f"[task:find_latest] START — run_id={run_id!r}")
        s3_key, file_name, file_size = find_latest_file()
        context["ti"].xcom_push(key="s3_key",       value=s3_key)
        context["ti"].xcom_push(key="file_name",    value=file_name)
        context["ti"].xcom_push(key="file_size_mb", value=round(file_size, 4))
        print(f"  [task:find_latest] XCom pushed — s3_key={s3_key!r} file_name={file_name!r} file_size_mb={file_size:.4f}")
        print(f"[task:find_latest] END")

    def _task_upload_file(**context):
        run_id = context.get("run_id", "unknown")
        ti = context["ti"]
        print(f"[task:upload_file] START — run_id={run_id!r}")
        s3_key    = ti.xcom_pull(task_ids="find_latest", key="s3_key")
        file_name = ti.xcom_pull(task_ids="find_latest", key="file_name")
        print(f"  [task:upload_file] XCom pulled — s3_key={s3_key!r} file_name={file_name!r}")
        buffer = None
        try:
            buffer  = download_file(s3_key)
            file_id = upload_to_toqan(file_name, buffer)
        finally:
            if buffer:
                buffer.close()
        ti.xcom_push(key="file_id", value=file_id)
        print(f"  [task:upload_file] XCom pushed — file_id={file_id!r}")
        print(f"[task:upload_file] END")

    def _task_start_analysis(**context):
        run_id = context.get("run_id", "unknown")
        ti = context["ti"]
        print(f"[task:start_analysis] START — run_id={run_id!r}")
        file_id = ti.xcom_pull(task_ids="upload_file", key="file_id")
        print(f"  [task:start_analysis] XCom pulled — file_id={file_id!r}")
        conv_id, request_id = create_analysis_conversation(file_id)
        ti.xcom_push(key="conversation_id", value=conv_id)
        ti.xcom_push(key="request_id", value=request_id)
        print(f"  [task:start_analysis] XCom pushed — conversation_id={conv_id!r} request_id={request_id!r}")
        print(f"[task:start_analysis] END")

    def _task_fetch_and_email(**context):
        run_id = context.get("run_id", "unknown")
        ti = context["ti"]
        print(f"[task:fetch_and_email] START — run_id={run_id!r}")
        conv_id   = ti.xcom_pull(task_ids="start_analysis", key="conversation_id")
        request_id = ti.xcom_pull(task_ids="start_analysis", key="request_id")
        file_name = ti.xcom_pull(task_ids="find_latest",    key="file_name")
        file_size = ti.xcom_pull(task_ids="find_latest",    key="file_size_mb") or 0.0
        file_id   = ti.xcom_pull(task_ids="upload_file",    key="file_id")
        print(f"  [task:fetch_and_email] XCom pulled — conv_id={conv_id!r} file_name={file_name!r}")
        analysis = wait_for_analysis(conv_id, request_id)
        send_report_email(file_name, analysis, MODEL_NAME)

        print("\n" + "=" * 60)
        print("PIPELINE SUMMARY")
        print("=" * 60)
        print(f"  File         : {file_name}")
        print(f"  File size    : {file_size:.2f} MB")
        print(f"  File ID      : {file_id}")
        print(f"  Conversation : {conv_id}")
        print(f"  Analysis len : {len(analysis):,} chars")
        print(f"  Output type  : {'HTML' if _is_html_output(analysis) else 'Plain text'}")
        print(f"  Recipients   : {RECIPIENT_EMAIL}")
        print(f"  Status       : SUCCESS")
        print("=" * 60 + "\n")
        print(f"[task:fetch_and_email] END")

    # ── DAG ───────────────────────────────────────────────────────────────

    DAG_DEFAULT_ARGS = {
        "owner": "data",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": dag_failure_callback,
    }

    with DAG(
        dag_id="fraud_insights",
        default_args=DAG_DEFAULT_ARGS,
        description="S3 → Toqan analysis → email (fraud monitoring insights)",
        schedule_interval="@daily",
        start_date=days_ago(1),
        catchup=False,
        max_active_runs=1,
        tags=["toqan", "fraud", "insights"],
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
