#!/usr/bin/env python3
"""
Toqan UpTop V3 monitoring insights — Apache Airflow DAG + pipeline helpers.

ARCHITECTURE NOTE — why this file does NOT ask an LLM to read the data file
anymore: two rounds of prompt-level fixes (anti-fabrication rules, exact
category-label whitelisting) drove label/category hallucination to zero, but
had no measurable effect on numeric hallucination — every PSI value, CSI
value, count, and rate in generated reports was still invented, just now
dressed in the correct real-world vocabulary. Asking a single LLM call to
both verbatim-retrieve 100+ precise numbers from a dense JSON file AND
compose a long, richly formatted report proved fundamentally unreliable.

The fix: every KPI, RAG status, table row, and alert is now computed
deterministically in Python (utils.uptop_v3_metrics.compute_report_metrics)
directly from the extractor's output. Toqan is only ever asked to write five
short narrative paragraphs around numbers it is handed already-computed
(generate_narrative) — it never looks anything up, so it can no longer
invent a PSI value, a feature name, or a count. The final HTML is rendered
in Python (utils.uptop_v3_html_renderer.render_html_report), with the
narrative slotted in as prose only.

Deploy: put this file (or repo) on Airflow's DAG path / PYTHONPATH. Ensure
`.env` is visible to `utils.utils` (repo root as cwd or symlink .env).

Airflow is imported only when available so `python uptop_v3.py` still works
locally without installing apache-airflow.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import re
import time
from datetime import datetime, timedelta
from io import BytesIO

import boto3

from config.settings import (
    RECIPIENT_EMAIL_UPTOP_V3,
    S3_BUCKET,
    S3_FOLDER_BAJAJ,
    SUPPORTED_EXTENSIONS,
)
from config.prompts import get_narrative_prompt
from email_service.template import EMAIL_CONFIG
from email_service.sender import send_email
from utils.helpers import get_analysis, make_api_request
from utils.uptop_v3_extractor import extract_from_html
from utils.uptop_v3_metrics import compute_report_metrics
from utils.uptop_v3_html_renderer import render_html_report
from utils.utils import TOQAN_API_KEY, TOQAN_BASE_URL
from email_service.error_notifier import dag_failure_callback, notify_error

MODEL_NAME = "uptop_v3"

NARRATIVE_KEYS = (
    "health_summary", "funnel_commentary", "psi_score_commentary",
    "csi_commentary", "disbursal_commentary",
)


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


def extract_and_compute_metrics(file_name, html_buffer):
    """
    Parse the downloaded UpTop V3 HTML report and compute every metric the
    report needs, entirely in Python. No LLM is involved anywhere in this
    function — `metrics` is exactly as trustworthy as the source HTML table
    data itself.

    Returns:
        dict: metrics (see utils.uptop_v3_metrics.compute_report_metrics),
              plus a top-level "_file_name" key for downstream email naming.
    """
    print("\n[extract_and_compute_metrics] START")
    html_buffer.seek(0)
    html_content = html_buffer.read().decode("utf-8")

    data = extract_from_html(html_content, source_label=file_name)
    metrics = compute_report_metrics(data)
    metrics["_file_name"] = file_name

    print(f"  [extract_and_compute_metrics] source: {file_name!r} ({html_buffer.getbuffer().nbytes:,} bytes)")
    print(f"  [extract_and_compute_metrics] latest_month: {metrics['meta'].get('latest_month')!r}")
    print(f"  [extract_and_compute_metrics] overall_status: {metrics['overall_rag']!r}")
    print(f"  [extract_and_compute_metrics] alerts computed: {len(metrics['alerts'])}")
    for alert in metrics["alerts"]:
        print(f"    - [{alert['status']}] {alert['metric']} = {alert['current_value']} (threshold {alert['threshold']})")
    print("[extract_and_compute_metrics] END")
    return metrics


def build_narrative_facts(metrics):
    """
    Build the compact facts payload handed to the LLM for narrative-only
    generation — a small, curated subset of `metrics` with the flags the
    model needs already computed (absent_in_disbursed, conversion_alert,
    etc.), so it never has to infer or look anything up, only narrate it.
    """
    kpi = metrics["kpi"]
    latest = metrics["meta"]["latest_month"]

    top_risk_gaps = sorted(
        [r for r in metrics["risk_segments"] if r["gap_pp"] is not None],
        key=lambda r: abs(r["gap_pp"]), reverse=True,
    )[:3]

    return {
        "latest_month": latest,
        "overall_status": metrics["overall_rag"],
        "kpi_summary": {
            "psi_credit_check": kpi["psi_credit_check"]["value"],
            "psi_disbursed": kpi["psi_disbursed"]["value"],
            "psi_disbursed_status": kpi["psi_disbursed"]["rag"],
            "disbursal_rate_pct": kpi["disbursal_rate"]["value"],
            "disbursal_rate_mom_pp": kpi["disbursal_rate"]["mom_delta_pp"],
            "rejection_rate_pct": kpi["rejection_rate"]["value"],
            "ca_lps_pct": kpi["ca_lps_pct"]["value"],
            "ca_lps_status": kpi["ca_lps_pct"]["rag"],
            "avg_ticket_size_lakhs": kpi["avg_ticket_size_lakhs"]["value"],
            "feature_csi_unstable_count": kpi["feature_csi_counts"]["unstable"],
            "feature_csi_marginal_count": kpi["feature_csi_counts"]["marginal"],
            "feature_csi_stable_count": kpi["feature_csi_counts"]["stable"],
        },
        "funnel_latest_month": [
            {"status": r["status"], "pct": r["percentages"].get(latest), "mom_delta_pp": r["mom_delta_pp"]}
            for r in metrics["funnel"]
        ],
        "psi_trend": metrics["psi_table"],
        "top_unstable_features": [
            {"feature": r["feature"], "csi_disbursed": r["disbursed_csi"], "delta_vs_credit_check": r["delta"]}
            for r in metrics["top_unstable_features"]
        ],
        "score_buckets_absent_in_disbursed": [
            b["bucket"] for b in metrics["score_buckets"]
            if b["absent_in_disbursed"] and (b["credit_check_pct"] or 0) > 0
        ],
        "risk_segments_absent_in_disbursed": [
            r["segment"] for r in metrics["risk_segments"]
            if r["absent_in_disbursed"] and (r["credit_check_pct"] or 0) > 0
        ],
        "risk_segment_top_gaps": [
            {"segment": r["segment"], "gap_pp": r["gap_pp"]} for r in top_risk_gaps
        ],
        "approval_methods": [
            {"method": r["method"], "credit_check_pct": r["credit_check_pct"],
             "disbursed_pct": r["disbursed_pct"], "gap_pp": r["gap_pp"],
             "conversion_alert": r["conversion_alert"], "fully_filtered_out": r["absent_in_disbursed"]}
            for r in metrics["approval_methods"]
        ],
        "disbursal_trend": metrics["disbursal_table"],
    }


def _parse_narrative_json(answer):
    """
    Parse Toqan's narrative response, tolerating a leaked <think>...</think>
    reasoning block or stray text before/after the JSON object — trust
    nothing about LLM output formatting, same defensive posture as the old
    HTML-detection guard this replaces.
    """
    text = (answer or "").strip()
    if text.lower().startswith("<think>"):
        end_tag = text.lower().find("</think>")
        if end_tag != -1:
            text = text[end_tag + len("</think>"):].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in narrative response: {text[:200]!r}")

    narrative = json.loads(text[start:end + 1])
    if not isinstance(narrative, dict):
        raise ValueError(f"Narrative response is not a JSON object: {type(narrative)}")
    return narrative


def generate_narrative(metrics):
    """
    Ask Toqan for narrative prose only, given pre-computed facts embedded
    directly in the prompt (no file upload — nothing left for Toqan to
    "read" incorrectly). Returns a dict with the keys in NARRATIVE_KEYS.

    Falls back to an empty dict on any failure (bad JSON, timeout, missing
    keys) so a flaky LLM call degrades the report's prose quality but never
    breaks the numbers or blocks the email — render_html_report() already
    supplies safe generic sentences for any missing narrative key.
    """
    print("\n[generate_narrative] START")
    facts = build_narrative_facts(metrics)
    facts_json = json.dumps(facts, separators=(",", ":"), default=str, ensure_ascii=False)
    print(f"  [generate_narrative] facts payload: {len(facts_json)} chars (embedded in prompt, no file upload)")

    try:
        prompt = get_narrative_prompt(facts_json)
        headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
        conv_response = make_api_request(
            "POST", f"{TOQAN_BASE_URL}/create_conversation", headers,
            json_data={"user_message": prompt},
        )
        full_resp = conv_response.json()
        conversation_id = full_resp["conversation_id"]
        request_id = full_resp["request_id"]
        print(f"  [generate_narrative] conversation_id={conversation_id!r} request_id={request_id!r}")

        time.sleep(10)
        answer = get_analysis(conversation_id, request_id, headers, TOQAN_BASE_URL)
        narrative = _parse_narrative_json(answer)

        missing_keys = [k for k in NARRATIVE_KEYS if k not in narrative or not str(narrative[k]).strip()]
        if missing_keys:
            print(f"  [generate_narrative] ⚠ WARNING — narrative missing/empty keys: {missing_keys} "
                  f"(safe generic sentences will be used for these sections)")

        print(f"  [generate_narrative] parsed narrative keys: {list(narrative.keys())}")
        print("[generate_narrative] END (success)")
        return narrative
    except Exception as e:
        print(f"  [generate_narrative] ⚠ WARNING — narrative generation failed, falling back "
              f"to generic prose for all sections. Error: {e}")
        print("[generate_narrative] END (fallback)")
        return {}


# Generic industry-standard patterns that do NOT exist anywhere in this
# model's actual data — if these show up in the LLM's narrative text, it's a
# strong signal it fell back on a "textbook" credit-risk template instead of
# narrating the given facts (the exact failure mode the original
# hallucination audit uncovered).
_HALLUCINATION_RED_FLAGS = [
    "300-350", "350-400", "400-450", "450-500", "500-550",
    "550-600", "600-650", "650-700", "700-750", "750-850",
    "cibil score band", "cibil band", "cibil range",
    "very low", "very high",
    "manual approval", "auto approval", "manually approved", "auto-approved",
    "manual/auto",
]

# Numbers with a decimal point, or 2+ digits — ignores lone single digits
# ("2", "0") which occur constantly by pure chance in any document this size
# and produced false "grounded" signals in the previous version of this
# check (verified against a real generated report during this rewrite).
_NUMBER_PATTERN = re.compile(r"\d+\.\d+|\d{2,}")


def log_grounding_diagnostics(metrics, narrative):
    """
    Post-hoc, NON-BLOCKING diagnostic log for the LLM-written narrative text
    only. The tables/KPIs/alerts in the report are Python-computed by
    construction and are therefore not re-checked here — this only flags
    (a) numbers in the narrative that were never supplied in the facts
    payload (i.e. the LLM inventing a figure instead of only narrating
    given ones), and (b) generic hallucination fingerprints.
    """
    print("\n" + "=" * 80)
    print("[GROUNDING CHECK] Checking LLM narrative text against the facts it was given")
    print("=" * 80)

    facts_json = json.dumps(build_narrative_facts(metrics), default=str)
    narrative_text = " ".join(str(v) for v in (narrative or {}).values())

    if not narrative_text.strip():
        print("  No narrative text to check (generation failed or was skipped — "
              "generic fallback sentences were used, which cannot hallucinate numbers).")
        print("=" * 80 + "\n")
        return

    narrative_numbers = set(_NUMBER_PATTERN.findall(narrative_text))
    invented = sorted(n for n in narrative_numbers if n not in facts_json)
    print(f"  Narrative mentions {len(narrative_numbers)} distinct number(s); "
          f"{len(invented)} not present anywhere in the facts given to the LLM.")
    if invented:
        print(f"  -> WARNING: possibly invented number(s) in narrative prose: {invented}")

    hits = [pat for pat in _HALLUCINATION_RED_FLAGS if pat in narrative_text.lower()]
    if hits:
        print(f"  -> RED FLAG: generic hallucination fingerprints in narrative: {hits}. "
              f"This suggests the model fell back on a generic industry template.")
    else:
        print("  No known generic hallucination fingerprints detected in narrative.")

    total_issues = len(invented) + len(hits)
    print("\n[GROUNDING CHECK] VERDICT: " +
          ("✓ Narrative fully grounded in the given facts." if total_issues == 0
           else f"⚠ {total_issues} issue(s) detected in the narrative prose — see warnings above. "
                f"Note: all tables/numbers in the report itself are Python-computed and unaffected."))
    print("=" * 80 + "\n")


def build_report_html(metrics, narrative):
    """Render the final self-contained HTML report from computed metrics + narrative prose."""
    return render_html_report(metrics, narrative)


def send_report_email(file_name, report_html, model_name=MODEL_NAME):
    """Send the rendered HTML report as the email body."""
    print("\n📧 Creating and sending email...")

    date_str = datetime.now().strftime('%Y-%m-%d')
    config = EMAIL_CONFIG.get(model_name, EMAIL_CONFIG["default"])
    subject = f"{config['title']} - {file_name} - {date_str}"

    text_body = (
        f"UpTop V3 Model Monitoring Report — {file_name} — {date_str}\n\n"
        f"Please view this email in an HTML-capable email client."
    )
    send_email(
        subject=subject,
        html_content=report_html,
        text_content=text_body,
        recipients=RECIPIENT_EMAIL_UPTOP_V3,
    )
    print("✓ HTML report sent as email body")


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
        metrics = extract_and_compute_metrics(file_name, buffer)
        narrative = generate_narrative(metrics)
        log_grounding_diagnostics(metrics, narrative)
        report_html = build_report_html(metrics, narrative)
        send_report_email(file_name, report_html, MODEL_NAME)

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
    # `metrics` is a plain JSON-serializable dict, so it XComs cleanly.

    def _task_find_latest(**context):
        s3_key, file_name, file_size = find_latest_file()
        context["ti"].xcom_push(key="s3_key",       value=s3_key)
        context["ti"].xcom_push(key="file_name",    value=file_name)
        context["ti"].xcom_push(key="file_size_mb", value=round(file_size, 4))

    def _task_extract_and_compute(**context):
        ti        = context["ti"]
        s3_key    = ti.xcom_pull(task_ids="find_latest", key="s3_key")
        file_name = ti.xcom_pull(task_ids="find_latest", key="file_name")
        buffer = None
        try:
            buffer = download_file(s3_key)
            metrics = extract_and_compute_metrics(file_name, buffer)
        finally:
            if buffer:
                buffer.close()
        ti.xcom_push(key="metrics", value=metrics)

    def _task_generate_narrative(**context):
        ti = context["ti"]
        metrics = ti.xcom_pull(task_ids="extract_and_compute", key="metrics")
        narrative = generate_narrative(metrics)
        ti.xcom_push(key="narrative", value=narrative)

    def _task_render_and_email(**context):
        ti        = context["ti"]
        metrics   = ti.xcom_pull(task_ids="extract_and_compute", key="metrics")
        narrative = ti.xcom_pull(task_ids="generate_narrative", key="narrative")
        file_name = ti.xcom_pull(task_ids="find_latest", key="file_name")

        log_grounding_diagnostics(metrics, narrative)
        report_html = build_report_html(metrics, narrative)
        send_report_email(file_name, report_html, MODEL_NAME)

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
        description="S3 → deterministic metrics → Toqan narrative → email (UpTop V3 model monitoring)",
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

        t2_extract_and_compute = PythonOperator(
            task_id="extract_and_compute",
            python_callable=_task_extract_and_compute,
        )

        t3_generate_narrative = PythonOperator(
            task_id="generate_narrative",
            python_callable=_task_generate_narrative,
        )

        t4_render_and_email = PythonOperator(
            task_id="render_and_email",
            python_callable=_task_render_and_email,
        )

        t1_find_latest >> t2_extract_and_compute >> t3_generate_narrative >> t4_render_and_email


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()
