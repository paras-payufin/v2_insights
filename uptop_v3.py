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
import time
from datetime import datetime, timedelta
from io import BytesIO

import boto3

from config.settings import (
    RECIPIENT_EMAIL_UPTOP_V3,
    S3_BUCKET,
    S3_FOLDER_UPTOP,
    SUPPORTED_EXTENSIONS,
)
from config.prompts import get_prompt_by_model_name
from email_service.template import create_html_email, EMAIL_CONFIG
from email_service.sender import send_email
from utils.helpers import extract_html_document, get_analysis, make_api_request, remove_emojis
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


def _briefing_user_message(prompt: str) -> str:
    """Step-1 message: instructions only — no JSON yet."""
    return (
        "You are about to receive a JSON monitoring dataset in my NEXT message "
        "in this same conversation.\n\n"
        "FIRST TASK (this message only):\n"
        "1. Read the report-generation instructions below carefully end-to-end.\n"
        "2. Internalize every rule (no inventing values, JSON-only source, "
        "inline HTML output, leadership-formal professional colors, "
        "30in6 vs 60in15 separation, section list, etc.).\n"
        "3. Do NOT generate the HTML report yet.\n"
        "4. Do NOT invent, estimate, or assume any data.\n"
        "5. Reply briefly confirming you understand the instructions and are "
        "ready for the JSON file in the next message.\n\n"
        "======== REPORT GENERATION INSTRUCTIONS ========\n\n"
        f"{prompt}\n\n"
        "======== END INSTRUCTIONS ========\n\n"
        "Confirm readiness only. Wait for the JSON attachment in the next message."
    )


def _generate_user_message() -> str:
    """Step-2 message: JSON is attached — produce the report now."""
    return (
        "The JSON data file is now attached. This is your only data source.\n\n"
        "Generate the complete stakeholder-ready HTML monitoring report now, "
        "following the instructions from my previous message exactly.\n"
        "- Use only values present in the attached JSON\n"
        "- If a value is missing, omit it or show "
        "\"Not available in supplied JSON\"\n"
        "- Return the full HTML inline in your answer "
        "(first characters must be <!DOCTYPE html>, last must be </html>)\n"
        "- Do NOT create a downloadable/saved file artifact or return only a filename\n"
        "- Do NOT invent or estimate missing values\n"
        "- Populate all required sections with embedded chart data\n"
        "- Keep visuals leadership-formal: professional navy/charcoal/slate "
        "palette only — no neon, pastel, playful, or emoji styling"
    )


def create_prompt_briefing():
    """
    Step 1: create a conversation with the report prompt only (no JSON).
    Returns (conversation_id, request_id).
    """
    print(f"\n[create_prompt_briefing] START — model={MODEL_NAME!r}")
    time.sleep(3)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    prompt = get_prompt_by_model_name(MODEL_NAME)
    user_message = _briefing_user_message(prompt)

    print(f"  [create_prompt_briefing] prompt length : {len(prompt)} chars")
    print(f"  [create_prompt_briefing] message length: {len(user_message)} chars")
    print(f"  [create_prompt_briefing] preview: {user_message[:120]!r}")

    conv_response = make_api_request(
        "POST",
        f"{TOQAN_BASE_URL}/create_conversation",
        headers,
        json_data={"user_message": user_message},
    )
    full_resp = conv_response.json()
    conversation_id = full_resp["conversation_id"]
    request_id = full_resp["request_id"]
    print(f"  [create_prompt_briefing] conversation_id={conversation_id!r}")
    print(f"  [create_prompt_briefing] request_id={request_id!r}")
    print("[create_prompt_briefing] END")
    return conversation_id, request_id


def continue_with_json(conversation_id, file_id):
    """
    Step 2: same conversation — attach JSON and ask to generate the report.
    Returns (conversation_id, request_id) for the new turn.
    """
    print(f"\n[continue_with_json] START — conversation_id={conversation_id!r}")
    time.sleep(3)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    user_message = _generate_user_message()
    payload = {
        "conversation_id": conversation_id,
        "user_message": user_message,
        "private_user_files": [{"id": file_id}],
    }
    print(f"  [continue_with_json] file_id={file_id!r}")
    print(f"  [continue_with_json] message preview: {user_message[:120]!r}")

    cont_response = make_api_request(
        "POST",
        f"{TOQAN_BASE_URL}/continue_conversation",
        headers,
        json_data=payload,
    )
    full_resp = cont_response.json()
    print(f"  [continue_with_json] full response: {full_resp}")
    request_id = full_resp["request_id"]
    # API should echo the same conversation_id; prefer response if present
    conversation_id = full_resp.get("conversation_id") or conversation_id
    print(f"  [continue_with_json] request_id={request_id!r}")
    print("[continue_with_json] END")
    return conversation_id, request_id


def start_two_step_analysis(file_id):
    """
    Two-step Toqan flow to reduce hallucination:
      1) Send prompt only → wait for readiness ack
      2) Continue same conversation with JSON attached → return that request_id
    """
    print("\n[start_two_step_analysis] START — two-step prompt → JSON flow")
    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}

    conversation_id, briefing_request_id = create_prompt_briefing()
    print("\n⏳ Step 1/2 — waiting for prompt-briefing acknowledgement...")
    time.sleep(10)
    briefing = get_analysis(
        conversation_id,
        briefing_request_id,
        headers,
        TOQAN_BASE_URL,
        require_html=False,
    )
    print(f"  [start_two_step_analysis] briefing ack ({len(briefing)} chars): "
          f"{briefing[:300]!r}")

    conversation_id, report_request_id = continue_with_json(conversation_id, file_id)
    print("[start_two_step_analysis] END — report generation started")
    return conversation_id, report_request_id


def wait_for_analysis(conversation_id, request_id):
    """Wait for and retrieve the HTML analysis from Toqan (step-2 request)."""
    print("\n⏳ Step 2/2 — waiting for HTML report (2-5 min)...")
    time.sleep(30)

    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    analysis = get_analysis(
        conversation_id, request_id, headers, TOQAN_BASE_URL, require_html=True
    )

    if not analysis:
        raise RuntimeError("No analysis received from Toqan")

    # Strip any leaked preamble/meta-commentary before the actual HTML document
    cleaned = extract_html_document(analysis)
    if cleaned != analysis:
        print(f"  [wait_for_analysis] Stripped leaked preamble/trailing text "
              f"({len(analysis) - len(cleaned)} chars removed)")
    if "<canvas" not in cleaned.lower():
        raise RuntimeError(
            f"UpTop V3 HTML report has no <canvas> charts ({len(cleaned)} chars). "
            "Toqan likely returned a header-only stub. Re-run or tighten the prompt."
        )
    return cleaned


def _is_html_output(text):
    """Return True when Toqan's output is a self-contained HTML document."""
    return text.strip()[:20].lower().lstrip().startswith(("<!doctype html", "<html"))


def _build_wrapper_email_body(file_name, date_str, model_name, report_filename):
    """Short HTML wrapper body used when the full report is attached as a file."""
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
        Please find the <strong>UpTop V3 Model Monitoring Report</strong> attached to this email.<br>
        Source file: <code>{file_name}</code> &nbsp;|&nbsp; Generated: {date_str}
      </p>
      <p style="color:#6b7280;font-size:13px;">
        Open the attached <code>{report_filename}</code> in any web browser to view the full
        interactive report including charts and monitoring insights.
      </p>
    </div>
    <div style="background:#f9fafb;padding:20px 40px;border-top:1px solid #e5e7eb;">
      <p style="color:#9ca3af;font-size:12px;margin:0;">{config.get('footer','Generated by Toqan Analysis Platform')}</p>
    </div>
  </div>
</body>
</html>"""


def send_report_email(file_name, analysis, model_name=MODEL_NAME):
    """Generate and send email report.

    HTML path  — report is attached as .html (not inlined as the email body).
    Plain text — analysis is wrapped with create_html_email template.
    """
    print("\n📧 Creating and sending email...")

    date_str = datetime.now().strftime('%Y-%m-%d')
    config = EMAIL_CONFIG.get(model_name, EMAIL_CONFIG["default"])
    subject = f"{config['title']} - {file_name} - {date_str}"

    if _is_html_output(analysis):
        report_filename = f"uptop_v3_model_monitoring_report_{date_str}.html"
        html_body = _build_wrapper_email_body(
            file_name, date_str, model_name, report_filename
        )
        text_body = (
            f"UpTop V3 Model Monitoring Report — {file_name} — {date_str}\n\n"
            f"Please find the HTML report attached ({report_filename}). "
            f"Open it in a web browser to view the full interactive report."
        )
        send_email(
            subject=subject,
            html_content=html_body,
            text_content=text_body,
            recipients=RECIPIENT_EMAIL_UPTOP_V3,
            attachment_bytes=analysis.encode("utf-8"),
            attachment_filename=report_filename,
        )
        print(f"✓ HTML report sent as attachment ({report_filename})")
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
    try:
        s3_key, file_name, _file_size = find_latest_file()
        buffer = download_file(s3_key)
        file_id = upload_to_toqan(file_name, buffer)
        conversation_id, request_id = start_two_step_analysis(file_id)
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
        buffer    = None
        try:
            buffer  = download_file(s3_key)
            file_id = upload_to_toqan(file_name, buffer)
        finally:
            if buffer:
                buffer.close()
        ti.xcom_push(key="file_id", value=file_id)

    def _task_start_analysis(**context):
        """Brief prompt first, then continue with JSON; push step-2 request_id."""
        ti      = context["ti"]
        file_id = ti.xcom_pull(task_ids="upload_file", key="file_id")
        conv_id, request_id = start_two_step_analysis(file_id)
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
