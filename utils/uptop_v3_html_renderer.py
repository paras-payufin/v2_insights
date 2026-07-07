#!/usr/bin/env python3
"""
UpTop V3 — deterministic HTML report renderer.

Takes the output of utils.uptop_v3_metrics.compute_report_metrics() (every
number, RAG status, and alert already computed in plain Python) plus a small
dict of LLM-written narrative prose, and renders the final 8-section HTML
report as plain string templating.

No number in the output HTML is ever supplied by the LLM — the narrative
strings only add qualitative colour around numbers that are already baked
into the tables before the narrative is even inserted. This is the
structural fix for the numeric-hallucination problem: the LLM cannot invent
a PSI value, feature name, or count here because it is never asked to
produce one.
"""

from html import escape

RAG_COLORS = {"Green": "#22c55e", "Amber": "#f59e0b", "Red": "#ef4444"}
NAVY = "#1e3a8a"

MONTH_NAMES = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
    "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


def _month_label(yyyy_mm):
    if not yyyy_mm or "-" not in yyyy_mm:
        return yyyy_mm or "\u2014"
    year, month = yyyy_mm.split("-")
    return f"{MONTH_NAMES.get(month, month)} {year}"


def _fmt(value, suffix="", ndigits=None, dash="\u2014"):
    if value is None:
        return dash
    if isinstance(value, float):
        if ndigits is not None:
            value = round(value, ndigits)
        if value == int(value):
            value = int(value) if ndigits == 0 else value
    return f"{value}{suffix}"


def _fmt_pct(value, ndigits=2):
    return _fmt(value, suffix="%", ndigits=ndigits)


def _fmt_count(value):
    if value is None:
        return "\u2014"
    return f"{int(value):,}"


_DASH = "\u2014"


def _badge(status):
    if not status or status not in RAG_COLORS:
        label = escape(status) if status else _DASH
        return f'<span class="badge badge-info">{label}</span>'
    color = RAG_COLORS[status]
    return f'<span class="badge" style="background:{color}">{escape(status)}</span>'


def _alert_status_badge(status):
    color = {"Critical": "#ef4444", "High Priority": "#ef4444", "Warning": "#f59e0b"}.get(status, "#6b7280")
    label = escape(status) if status else _DASH
    return f'<span class="badge" style="background:{color}">{label}</span>'


def _delta_span(value, color=None, suffix="pp"):
    if value is None:
        return "\u2014"
    sign = "+" if value > 0 else ""
    css_color = {"red": "#ef4444", "green": "#16a34a"}.get(color, "#374151")
    return f'<span style="color:{css_color};font-weight:600">{sign}{value}{suffix}</span>'


# ---------------------------------------------------------------------------
# Deterministic prose that IS just a restatement of already-computed numbers
# (kept out of the LLM's hands entirely — no lookup required, so no
# hallucination risk, and it guarantees the summary bullets are internally
# consistent with the tables above them).
# ---------------------------------------------------------------------------

def generate_summary_bullets(metrics):
    kpi = metrics["kpi"]
    bullets = []

    dis_psi = kpi["psi_disbursed"]
    psi_table = metrics["psi_table"]
    if dis_psi["rag"] in ("Amber", "Red") and len(psi_table) >= 2:
        prior_val = psi_table[-2]["disbursed_psi"]
        threshold_note = ">0.20 critical" if dis_psi["rag"] == "Red" else "0.10\u20130.20 warning"
        bullets.append(
            f"Disbursed PSI at {dis_psi['value']} "
            f"({'up' if prior_val is not None and dis_psi['value'] > prior_val else 'vs'} "
            f"{_fmt(prior_val)} prior month) — {dis_psi['rag']} status ({threshold_note} threshold)."
        )

    ca_lps = kpi["ca_lps_pct"]
    if ca_lps["rag"] in ("Amber", "Red"):
        direction = "up" if (ca_lps["mom_delta_pp"] or 0) >= 0 else "down"
        threshold_note = ">30% critical" if ca_lps["rag"] == "Red" else "15\u201330% warning"
        abs_delta = abs(ca_lps["mom_delta_pp"]) if ca_lps["mom_delta_pp"] is not None else None
        bullets.append(
            f"CA But LPS Not Done at {_fmt_pct(ca_lps['value'])} — {direction} "
            f"{_fmt(abs_delta, suffix='pp')} MoM — "
            f"{ca_lps['rag']} status ({threshold_note} threshold)."
        )

    csi_counts = kpi["feature_csi_counts"]
    if csi_counts["unstable"] > 0:
        total = csi_counts["unstable"] + csi_counts["marginal"] + csi_counts["stable"]
        top = metrics["top_unstable_features"][:3]
        top_str = "; ".join(f"{r['feature']} ({r['disbursed_csi']})" for r in top)
        bullets.append(
            f"{csi_counts['unstable']} of {total} features Unstable in Disbursed (CSI > 0.25) — "
            f"led by {top_str}."
        )

    for row in metrics["approval_methods"]:
        if row["conversion_alert"]:
            note = "fully filtered out of disbursals" if row["absent_in_disbursed"] else "a critical conversion gap"
            bullets.append(
                f"Approval method {row['method']} — {_fmt_pct(row['credit_check_pct'])} of Credit Check "
                f"applications but only {_fmt_pct(row['disbursed_pct'])} of Disbursed — {note}."
            )

    for row in metrics["risk_segments"]:
        if row["absent_in_disbursed"] and (row["credit_check_pct"] or 0) > 0:
            bullets.append(
                f"Risk segment {row['segment']} — {_fmt_pct(row['credit_check_pct'])} of Credit Check "
                f"applications — has zero disbursed applications; entirely absent from the Disbursed population."
            )

    for row in metrics["score_buckets"]:
        if row["absent_in_disbursed"] and (row["credit_check_pct"] or 0) > 0:
            bullets.append(
                f"V3 score bucket {row['bucket']} — {_fmt_pct(row['credit_check_pct'])} of Credit Check "
                f"applications — has zero disbursed applications."
            )

    disbursal_rate = kpi["disbursal_rate"]
    if disbursal_rate["value"] is not None:
        bullets.append(
            f"Disbursal rate at {_fmt_pct(disbursal_rate['value'])} "
            f"({_delta_span(disbursal_rate['mom_delta_pp'])} MoM)."
        )

    if not bullets:
        bullets.append("No critical or warning findings — all metrics within acceptable range.")

    return bullets[:6]


_OWNER_KEYWORDS = [
    (("psi", "csi"), "Data Science"),
    (("risk segment", "approval method"), "Risk + Product"),
    (("disbursal rate",), "Business + Analytics"),
    (("ca but lps", "rejection rate", "v3 score bucket"), "Operations + Product"),
]


def _owner_for_metric(metric_text):
    m = metric_text.lower()
    for keywords, owner in _OWNER_KEYWORDS:
        if any(k in m for k in keywords):
            return owner
    return "Operations + Product"


def _priority_and_timeline(status):
    if status in ("Critical", "High Priority"):
        return "P0", "Week 1"
    if status == "Warning":
        return "P1", "Week 2\u20133"
    return "P2", "Week 3\u20134"


def generate_action_table(metrics):
    rows = []
    for alert in metrics["alerts"]:
        owner = _owner_for_metric(alert["metric"])
        priority, timeline = _priority_and_timeline(alert["status"])
        rows.append({
            "finding": f"{alert['metric']} ({alert['status']})",
            "action": alert["recommended_action"],
            "owner": owner,
            "priority": priority,
            "timeline": timeline,
        })
    return rows


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_header(metrics, narrative):
    meta = metrics["meta"]
    overall = metrics["overall_rag"]
    return f"""
<div class="card header-card">
  <h1>UpTop V3 Model Monitoring Report — Bajaj Credit Check | Live Pipeline Data</h1>
  <div class="header-meta">
    <div><strong>Reporting Period:</strong> {escape(_month_label(meta['latest_month']))}</div>
    <div><strong>Model Purpose:</strong> Risk Model</div>
    <div><strong>Overall Status:</strong> {_badge(overall)}</div>
  </div>
</div>"""


def _render_kpi_summary(metrics, narrative):
    kpi = metrics["kpi"]

    def _tile(label, value_html, rag, extra=""):
        badge = _badge(rag) if rag and rag != "Informational" else (
            '<span class="badge badge-info">Informational</span>' if rag == "Informational" else ""
        )
        return f"""
    <div class="kpi-tile">
      <div class="kpi-label">{escape(label)}</div>
      <div class="kpi-value">{value_html}</div>
      <div class="kpi-badge">{badge}{extra}</div>
    </div>"""

    csi = kpi["feature_csi_counts"]
    tiles = "".join([
        _tile("PSI (Credit Check)", _fmt(kpi["psi_credit_check"]["value"], ndigits=4), "Informational"),
        _tile("PSI (Disbursed)", _fmt(kpi["psi_disbursed"]["value"], ndigits=4), kpi["psi_disbursed"]["rag"]),
        _tile("Disbursal Rate", _fmt_pct(kpi["disbursal_rate"]["value"]), kpi["disbursal_rate"]["rag"],
              extra=f" {_delta_span(kpi['disbursal_rate']['mom_delta_pp'])}"),
        _tile("Rejection Rate", _fmt_pct(kpi["rejection_rate"]["value"]), kpi["rejection_rate"]["rag"],
              extra=f" {_delta_span(kpi['rejection_rate']['mom_delta_pp'])}"),
        _tile("CA But LPS Not Done %", _fmt_pct(kpi["ca_lps_pct"]["value"]), kpi["ca_lps_pct"]["rag"],
              extra=f" {_delta_span(kpi['ca_lps_pct']['mom_delta_pp'])}"),
        _tile("Average Ticket Size (Disbursed)", f"\u20b9{_fmt(kpi['avg_ticket_size_lakhs']['value'])} Lakhs", None),
        _tile("Feature CSI (Disbursed)",
              f"{csi['unstable']} Unstable / {csi['marginal']} Marginal / {csi['stable']} Stable",
              csi["rag"]),
    ])

    health_summary = narrative.get("health_summary") or (
        f"Overall status is {metrics['overall_rag']} based on Disbursed PSI, Disbursed CSI, and funnel metrics. "
        f"See Early Warning Alerts below for the full list of breached thresholds."
    )

    return f"""
<div class="card">
  <h2>KPI Summary</h2>
  <div class="kpi-grid">{tiles}
  </div>
  <p class="narrative">{escape(health_summary)}</p>
</div>"""


def _render_funnel(metrics, narrative):
    months = metrics["meta"]["recent_months"]
    header_cols = "".join(
        f"<th>{escape(_month_label(m))} Count</th><th>{escape(_month_label(m))} %</th>" for m in months
    )
    rows_html = ""
    for row in metrics["funnel"]:
        cells = "".join(
            f"<td>{_fmt_count(row['counts'].get(m))}</td><td>{_fmt_pct(row['percentages'].get(m))}</td>"
            for m in months
        )
        rows_html += (
            f"<tr><td>{escape(row['status'])}</td>{cells}"
            f"<td>{_delta_span(row['mom_delta_pp'], row['delta_color'])}</td></tr>"
        )

    commentary = narrative.get("funnel_commentary") or (
        "See the funnel table above for month-over-month movement across every application status."
    )

    return f"""
<div class="card">
  <h2>Application Pipeline Funnel</h2>
  <table>
    <thead><tr><th>Application Status</th>{header_cols}<th>MoM \u0394 (pp)</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="narrative">{escape(commentary)}</p>
</div>"""


def _render_psi_and_score(metrics, narrative):
    psi_rows = "".join(
        f"<tr><td>{escape(_month_label(r['month']))}</td>"
        f"<td>{_fmt(r['credit_check_psi'], ndigits=4)} <span class='badge badge-info'>Informational</span></td>"
        f"<td>{_fmt(r['disbursed_psi'], ndigits=4)} {_badge(r['disbursed_rag'])}</td></tr>"
        for r in metrics["psi_table"]
    )

    absent_note = " <span class='badge badge-info'>absent in Disbursed</span>"
    bucket_rows = "".join(
        f"<tr class=\"{'absent-row' if b['absent_in_disbursed'] else ''}\">"
        f"<td>{escape(b['bucket'])}</td><td>{_fmt_pct(b['credit_check_pct'])}</td>"
        f"<td>{_fmt_pct(b['disbursed_pct'])}"
        f"{absent_note if b['absent_in_disbursed'] else ''}"
        f"</td></tr>"
        for b in metrics["score_buckets"]
    )

    commentary = narrative.get("psi_score_commentary") or (
        "See the PSI trend and score bucket distribution tables above."
    )

    return f"""
<div class="card">
  <h2>PSI & Score Distribution</h2>
  <table>
    <thead><tr><th>Month</th><th>Credit Check PSI</th><th>Disbursed PSI</th></tr></thead>
    <tbody>{psi_rows}</tbody>
  </table>
  <h3>V3 Score Distribution ({escape(_month_label(metrics['meta']['latest_month']))})</h3>
  <table>
    <thead><tr><th>V3 Score Bucket</th><th>Credit Check %</th><th>Disbursed %</th></tr></thead>
    <tbody>{bucket_rows}</tbody>
  </table>
  <p class="narrative">{escape(commentary)}</p>
</div>"""


def _render_csi(metrics, narrative):
    rows_html = ""
    for r in metrics["csi_table"]:
        row_class = "row-red" if r["disbursed_status"] == "Unstable" else (
            "row-amber" if r["disbursed_status"] == "Marginal" else ""
        )
        rows_html += (
            f"<tr class=\"{row_class}\"><td>{escape(r['feature'])}</td>"
            f"<td>{_fmt(r['credit_check_csi'], ndigits=4)}</td><td>\u2014</td>"
            f"<td>{_fmt(r['disbursed_csi'], ndigits=4)}</td><td>{_badge(r['disbursed_status'])}</td>"
            f"<td>{_delta_span(r['delta'], 'red' if (r['delta'] or 0) > 0 else 'green', suffix='')}</td></tr>"
        )

    top3 = metrics["top_unstable_features"][:3]
    top3_html = "".join(
        f"<li>{escape(r['feature'])} — CSI: {_fmt(r['disbursed_csi'], ndigits=4)} ({escape(r['disbursed_status'])})</li>"
        for r in top3
    )

    commentary = narrative.get("csi_commentary") or (
        "See the feature stability table above for CSI values and adverse-selection deltas."
    )

    return f"""
<div class="card">
  <h2>CSI & Feature Stability</h2>
  <table>
    <thead><tr><th>Feature Name</th><th>CSI (Credit Check)</th><th>CC Status</th>
      <th>CSI (Disbursed)</th><th>Disbursed Status</th><th>\u0394</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <h3>Top 3 Most Unstable Features (Disbursed)</h3>
  <ol>{top3_html}</ol>
  <p class="narrative">{escape(commentary)}</p>
</div>"""


def _render_disbursal_and_risk(metrics, narrative):
    double_risk_note = " <span class='badge' style='background:#ef4444'>Double-risk</span>"
    disb_rows = "".join(
        f"<tr><td>{escape(_month_label(r['month']))}</td><td>{_fmt_count(r['count'])}</td>"
        f"<td>\u20b9{_fmt(r['amount_cr'], ndigits=2)} Cr</td><td>\u20b9{_fmt(r['avg_ticket_lakhs'], ndigits=2)} Lakhs</td>"
        f"<td>{_fmt_pct(r['disbursal_rate_pct'])}"
        f"{double_risk_note if r['double_risk_event'] else ''}"
        f"</td></tr>"
        for r in metrics["disbursal_table"]
    )

    risk_rows = "".join(
        f"<tr class=\"{'absent-row' if r['absent_in_disbursed'] else ''}\">"
        f"<td>{escape(r['segment'])}</td><td>{_fmt_pct(r['credit_check_pct'])}</td>"
        f"<td>{_fmt_pct(r['disbursed_pct'])}</td><td>{_delta_span(r['gap_pp'], 'red' if (r['gap_pp'] or 0) < 0 else 'green')}</td></tr>"
        for r in metrics["risk_segments"]
    )

    approval_rows = "".join(
        f"<tr class=\"{'row-red' if r['conversion_alert'] else ''}\">"
        f"<td>{escape(r['method'])}</td><td>{_fmt_pct(r['credit_check_pct'])}</td>"
        f"<td>{_fmt_pct(r['disbursed_pct'])}</td><td>{_delta_span(r['gap_pp'], 'red' if (r['gap_pp'] or 0) < 0 else 'green')}</td></tr>"
        for r in metrics["approval_methods"]
    )

    commentary = narrative.get("disbursal_commentary") or (
        "See the disbursal, risk segment, and approval method tables above for adverse-selection signals."
    )

    return f"""
<div class="card">
  <h2>Disbursal & Risk Segment Analysis</h2>
  <h3>Monthly Disbursal Performance</h3>
  <table>
    <thead><tr><th>Month</th><th>Application Count</th><th>Amount Disbursed (\u20b9 Cr)</th>
      <th>Avg Ticket Size (\u20b9 Lakhs)</th><th>Disbursal Rate (%)</th></tr></thead>
    <tbody>{disb_rows}</tbody>
  </table>
  <h3>Risk Segment Distribution ({escape(_month_label(metrics['meta']['latest_month']))})</h3>
  <table>
    <thead><tr><th>Risk Segment</th><th>Credit Check %</th><th>Disbursed %</th><th>Gap (pp)</th></tr></thead>
    <tbody>{risk_rows}</tbody>
  </table>
  <h3>Approval Method Conversion ({escape(_month_label(metrics['meta']['latest_month']))})</h3>
  <table>
    <thead><tr><th>Approval Method</th><th>Credit Check %</th><th>Disbursed %</th><th>Conversion Gap (pp)</th></tr></thead>
    <tbody>{approval_rows}</tbody>
  </table>
  <p class="narrative">{escape(commentary)}</p>
</div>"""


def _render_alerts(metrics, narrative):
    alerts = metrics["alerts"]
    if not alerts:
        body = "<tr><td colspan=5>No active alerts — all metrics within acceptable range.</td></tr>"
    else:
        body = "".join(
            f"<tr><td>{escape(a['metric'])}</td><td>{escape(str(a['current_value']))}</td>"
            f"<td>{escape(str(a['threshold']))}</td><td>{_alert_status_badge(a['status'])}</td>"
            f"<td>{escape(a['recommended_action'])}</td></tr>"
            for a in alerts
        )

    return f"""
<div class="card">
  <h2>Early Warning Alerts</h2>
  <table>
    <thead><tr><th>Metric</th><th>Current Value</th><th>Threshold</th><th>Status</th><th>Recommended Action</th></tr></thead>
    <tbody>{body}</tbody>
  </table>
</div>"""


def _render_summary_and_actions(metrics, narrative):
    bullets = generate_summary_bullets(metrics)
    bullets_html = "".join(f"<li>{escape(b)}</li>" for b in bullets)

    actions = generate_action_table(metrics)
    actions_html = "".join(
        f"<tr><td>{escape(a['finding'])}</td><td>{escape(a['action'])}</td>"
        f"<td>{escape(a['owner'])}</td><td>{escape(a['priority'])}</td><td>{escape(a['timeline'])}</td></tr>"
        for a in actions
    ) or "<tr><td colspan=5>No actions required.</td></tr>"

    review_date = metrics["meta"]["next_review_date"] or "\u2014"

    return f"""
<div class="card">
  <h2>Summary & Actions</h2>
  <ul>{bullets_html}</ul>
  <h3>Recommended Actions</h3>
  <table>
    <thead><tr><th>Finding</th><th>Recommended Action</th><th>Owner</th><th>Priority</th><th>Timeline</th></tr></thead>
    <tbody>{actions_html}</tbody>
  </table>
  <p><strong>Next Scheduled Review:</strong> {escape(review_date)}</p>
</div>"""


_STYLE = f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px; background: #f3f4f6; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: #111827; }}
.card {{ background: #ffffff; border-radius: 10px; padding: 24px 28px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.header-card {{ background: {NAVY}; color: #ffffff; }}
.header-card h1 {{ margin: 0 0 12px; font-size: 22px; }}
.header-meta {{ display: flex; gap: 28px; flex-wrap: wrap; font-size: 14px; }}
h2 {{ color: {NAVY}; font-size: 18px; margin: 0 0 16px; border-bottom: 2px solid #e5e7eb; padding-bottom: 8px; }}
h3 {{ color: {NAVY}; font-size: 15px; margin: 20px 0 10px; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 13px; }}
th {{ background: {NAVY}; color: #ffffff; text-align: left; padding: 8px 10px; }}
td {{ padding: 7px 10px; border-bottom: 1px solid #e5e7eb; }}
tbody tr:nth-child(even) {{ background: #f9fafb; }}
tr.row-red {{ background: #fef2f2 !important; }}
tr.row-amber {{ background: #fffbeb !important; }}
tr.absent-row {{ background: #fef2f2 !important; }}
.badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; color: #fff; font-size: 12px; font-weight: 600; }}
.badge-info {{ background: #6b7280; }}
.narrative {{ color: #374151; line-height: 1.6; margin-top: 12px; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 8px; }}
.kpi-tile {{ background: #f9fafb; border-radius: 8px; padding: 14px; }}
.kpi-label {{ font-size: 12px; color: #6b7280; margin-bottom: 6px; }}
.kpi-value {{ font-size: 20px; font-weight: 700; color: {NAVY}; margin-bottom: 6px; }}
ul, ol {{ line-height: 1.7; }}
"""


def render_html_report(metrics, narrative=None):
    """
    Render the complete 8-section self-contained HTML report.

    metrics:   output of utils.uptop_v3_metrics.compute_report_metrics(data)
    narrative: optional dict of LLM-written prose keyed by
               health_summary / funnel_commentary / psi_score_commentary /
               csi_commentary / disbursal_commentary. Any missing key falls
               back to a safe generic sentence so the report always renders
               even if narrative generation fails or is skipped.
    """
    narrative = narrative or {}

    sections = [
        _render_header(metrics, narrative),
        _render_kpi_summary(metrics, narrative),
        _render_funnel(metrics, narrative),
        _render_psi_and_score(metrics, narrative),
        _render_csi(metrics, narrative),
        _render_disbursal_and_risk(metrics, narrative),
        _render_alerts(metrics, narrative),
        _render_summary_and_actions(metrics, narrative),
    ]

    title = "UpTop V3 Model Monitoring Report"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
{''.join(sections)}
</body>
</html>"""
