"""
DAG failure email notifier.

Airflow exposes on_failure_callback hooks at both the task level (via
default_args) and the DAG level. This module provides:

  dag_failure_callback(context)  — drop directly into default_args or any
                                   task/DAG on_failure_callback kwarg.
                                   Airflow injects a rich context dict
                                   (exception, task_instance, log_url, etc.).

  notify_error(exc, context)     — call from plain Python / CLI code where no
                                   Airflow context is available.

Both ultimately call _send_error_email(), which fires an HTML alert email
via the existing send_email() infrastructure.

Example — attach to every task in the DAG via default_args:
    from email_service.error_notifier import dag_failure_callback
    default_args = {
        "on_failure_callback": dag_failure_callback,
        ...
    }

Example — CLI / local main():
    from email_service.error_notifier import notify_error
    try:
        run_pipeline()
    except Exception as exc:
        notify_error(exc)
        raise
"""

import traceback
from datetime import datetime, timezone

from config.settings import SENDER_EMAIL
from email_service.sender import send_email

# ── Recipients ────────────────────────────────────────────────────────────────
ERROR_RECIPIENTS = ["paras.verma@payufin.com"]

# ── Public API ────────────────────────────────────────────────────────────────

def dag_failure_callback(context: dict) -> None:
    """
    Airflow on_failure_callback hook.

    Airflow calls this automatically whenever a task fails (after all retries
    are exhausted). Pass it to default_args so it applies to every task:

        default_args = {"on_failure_callback": dag_failure_callback, ...}

    Or attach it to individual tasks / the @dag decorator directly.
    """
    exc: BaseException | None = context.get("exception")

    ti = context.get("task_instance")
    dag_id   = getattr(ti, "dag_id",   None) or context.get("dag").dag_id
    task_id  = getattr(ti, "task_id",  None)
    run_id   = getattr(ti, "run_id",   None) or str(context.get("run_id", ""))
    try_num  = getattr(ti, "try_number", None)
    log_url  = getattr(ti, "log_url",   None)
    exec_date = context.get("execution_date") or context.get("logical_date")

    airflow_info = {
        "dag_id":           dag_id,
        "task_id":          task_id,
        "run_id":           run_id,
        "try_number":       try_num,
        "execution_date":   str(exec_date) if exec_date else None,
        "log_url":          log_url,
    }

    _send_error_email(exc=exc, airflow_context=airflow_info)


def notify_error(exc: Exception, source: str = "CLI / main()") -> None:
    """
    Send an error alert from non-Airflow code (local runs, unit tests, etc.).

    Args:
        exc:    The exception that was raised.
        source: Human-readable label shown in the email (e.g. function name).
    """
    _send_error_email(exc=exc, airflow_context=None, source=source)


# ── Internal ──────────────────────────────────────────────────────────────────

def _send_error_email(
    exc: BaseException | None,
    airflow_context: dict | None,
    source: str = "",
) -> None:
    """Build and dispatch the error email."""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    error_type    = type(exc).__name__ if exc else "Unknown"
    error_message = str(exc) if exc else "No exception details available."
    tb_text       = (
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if exc
        else "No traceback."
    )

    # ── Build email subject ────────────────────────────────────────────────
    if airflow_context:
        dag_id  = airflow_context.get("dag_id", "")
        task_id = airflow_context.get("task_id", "")
        subject = f"[AIRFLOW FAILURE] {dag_id} > {task_id} — {error_type} ({now})"
        origin  = f"DAG: {dag_id}  |  Task: {task_id}"
    else:
        subject = f"[PIPELINE FAILURE] toqan_fraud_insights — {error_type} ({now})"
        origin  = source or "Local / CLI"

    # ── Build HTML ─────────────────────────────────────────────────────────
    html = _build_error_html(
        subject=subject,
        origin=origin,
        error_type=error_type,
        error_message=error_message,
        traceback_text=tb_text,
        airflow_context=airflow_context,
        timestamp=now,
    )

    plain = (
        f"{subject}\n\n"
        f"Origin : {origin}\n"
        f"Error  : {error_type}: {error_message}\n\n"
        f"Traceback:\n{tb_text}"
    )

    try:
        send_email(
            subject=subject,
            html_content=html,
            text_content=plain,
            recipients=ERROR_RECIPIENTS,
        )
        print(f"[error_notifier] Alert sent to {ERROR_RECIPIENTS}")
    except Exception as mail_exc:
        # Never let the notifier itself crash the caller.
        print(f"[error_notifier] WARNING: failed to send alert email: {mail_exc}")


def _build_error_html(
    subject: str,
    origin: str,
    error_type: str,
    error_message: str,
    traceback_text: str,
    airflow_context: dict | None,
    timestamp: str,
) -> str:
    """Return a minimal, readable HTML error email."""

    # Airflow-specific rows (shown only when context is present)
    af_rows = ""
    if airflow_context:
        def _row(label, val):
            if val is None:
                return ""
            link = ""
            if label == "Log URL" and val:
                link = f'<a href="{val}" style="color:#4a9eda;">View logs</a>'
                return (
                    f'<tr><td style="padding:6px 12px;color:#888;white-space:nowrap;">{label}</td>'
                    f'<td style="padding:6px 12px;">{link}</td></tr>'
                )
            return (
                f'<tr><td style="padding:6px 12px;color:#888;white-space:nowrap;">{label}</td>'
                f'<td style="padding:6px 12px;font-family:monospace;font-size:13px;">{val}</td></tr>'
            )

        af_rows = f"""
        <tr><td colspan="2" style="padding:10px 12px 4px;font-weight:600;color:#ccc;
            border-top:1px solid #2a2a2a;font-size:12px;text-transform:uppercase;
            letter-spacing:.05em;">Airflow Context</td></tr>
        {_row("DAG",            airflow_context.get("dag_id"))}
        {_row("Task",           airflow_context.get("task_id"))}
        {_row("Run ID",         airflow_context.get("run_id"))}
        {_row("Execution Date", airflow_context.get("execution_date"))}
        {_row("Attempt",        airflow_context.get("try_number"))}
        {_row("Log URL",        airflow_context.get("log_url"))}
        """

    # Escape traceback for HTML
    safe_tb = (
        traceback_text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Pipeline Failure</title>
</head>
<body style="margin:0;padding:0;background:#0f0f0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e0e0e0;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0f0f;padding:32px 0;">
    <tr><td align="center">
      <table width="620" cellpadding="0" cellspacing="0"
             style="background:#1a1a1a;border-radius:8px;overflow:hidden;
                    border:1px solid #2a2a2a;max-width:620px;">

        <!-- Header -->
        <tr>
          <td style="background:#8b1a1a;padding:20px 28px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;
                               color:#ffaaaa;margin-bottom:4px;">Pipeline Alert</div>
                  <div style="font-size:18px;font-weight:600;color:#fff;">Task Failure Detected</div>
                </td>
                <td align="right">
                  <div style="font-size:11px;color:#ffaaaa;">{timestamp}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Origin strip -->
        <tr>
          <td style="background:#222;padding:10px 28px;border-bottom:1px solid #2a2a2a;">
            <span style="font-size:12px;color:#888;">Origin: </span>
            <span style="font-size:12px;font-family:monospace;color:#ccc;">{origin}</span>
          </td>
        </tr>

        <!-- Error summary -->
        <tr>
          <td style="padding:24px 28px 0;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;
                         color:#888;margin-bottom:10px;">Error</div>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border-radius:6px;overflow:hidden;border:1px solid #2a2a2a;">
              <tr>
                <td style="padding:6px 12px;color:#888;white-space:nowrap;width:130px;">Type</td>
                <td style="padding:6px 12px;font-family:monospace;font-size:13px;
                            color:#ff6b6b;">{error_type}</td>
              </tr>
              <tr style="background:#1f1f1f;">
                <td style="padding:6px 12px;color:#888;vertical-align:top;">Message</td>
                <td style="padding:6px 12px;font-size:13px;">{error_message}</td>
              </tr>
              {af_rows}
            </table>
          </td>
        </tr>

        <!-- Traceback -->
        <tr>
          <td style="padding:24px 28px 0;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;
                         color:#888;margin-bottom:10px;">Traceback</div>
            <pre style="margin:0;padding:16px;background:#111;border-radius:6px;
                         border:1px solid #2a2a2a;font-size:11.5px;line-height:1.6;
                         color:#ccc;overflow-x:auto;white-space:pre-wrap;
                         word-break:break-word;">{safe_tb}</pre>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:24px 28px;border-top:1px solid #2a2a2a;margin-top:24px;">
            <p style="margin:0;font-size:11px;color:#555;">
              Sent automatically by the Toqan fraud insights pipeline.
              Do not reply to this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""
