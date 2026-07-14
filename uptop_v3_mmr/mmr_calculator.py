#!/usr/bin/env python3
"""
mmr_calculator.py
Reads extracted raw data JSON, performs all MMR calculations, writes mmr_calculated.json.

Usage:
    python3 -m uptop_v3_mmr.mmr_calculator [--input path] [--output path]
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

MONTH_ABBREV = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


# ─────────────────────────────────────────────────────────────
# RAG helpers
# ─────────────────────────────────────────────────────────────

def rag_psi_disbursed(value):
    if value is None:
        return None
    if value < 0.10:
        return "Green"
    if value <= 0.20:
        return "Amber"
    return "Red"


def rag_ca_lps(pct):
    if pct is None:
        return None
    if pct > 30:
        return "Red"
    if pct >= 15:
        return "Amber"
    return "Green"


def rag_disbursal_rate(drop_pp):
    # drop_pp = DR_prior - DR_current (positive = fell)
    if drop_pp is None:
        return None
    if drop_pp <= 3:
        return "Green"
    if drop_pp <= 5:
        return "Amber"
    return "Red"


def rag_rejection_rate(delta_pp):
    # delta_pp = RR_current - RR_prior
    if delta_pp is None:
        return None
    if delta_pp > 3:
        return "Amber"
    return "Green"


def rag_feature_csi(unstable_count, marginal_count):
    if unstable_count > 0:
        return "Red"
    if marginal_count > 0:
        return "Amber"
    return "Green"


def classify_csi(value):
    if value is None:
        return None
    if value > 0.25:
        return "Unstable"
    if value >= 0.10:
        return "Marginal"
    return "Stable"


def worst_rag(*rags):
    order = {"Red": 3, "Amber": 2, "Green": 1, "Informational": 0, None: 0}
    ranked = [r for r in rags if r and r != "Informational"]
    if not ranked:
        return "Green"
    return max(ranked, key=lambda r: order.get(r, 0))


def month_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{MONTH_ABBREV[int(month)]} {year}"


def reporting_month_label(ym: str) -> str:
    year, month = ym.split("-")
    full = calendar.month_name[int(month)]
    return f"{full} {year}"


def format_report_date(iso_date: str) -> str:
    """Convert YYYY-MM-DD → '22 Jun 2026'. Falls back to input if unparseable."""
    if not iso_date:
        return ""
    try:
        y, m, d = iso_date.split("-")
        return f"{int(d)} {MONTH_ABBREV[int(m)]} {y}"
    except (ValueError, KeyError, IndexError):
        return iso_date


def next_review_date_str(latest_ym: str) -> str:
    year, month = map(int, latest_ym.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    last_date = date(year, month, last_day)
    next_review = last_date + timedelta(days=14)
    # Cross-platform day-without-leading-zero
    return f"{next_review.day} {calendar.month_name[next_review.month]} {next_review.year}"


def _resolve_months(raw: dict) -> tuple[list, str, str]:
    meta = raw.get("metadata") or {}
    months = meta.get("months")
    if not months:
        # Discover from status_counts columns
        status = (raw.get("credit_check") or {}).get("status_counts") or {}
        discovered = set()
        for row in status.values():
            for col in row.keys():
                if MONTH_RE.match(str(col)):
                    discovered.add(col)
        months = sorted(discovered)

    if not months:
        raise ValueError("No months found in raw data")

    latest = meta.get("latest_month") or months[-1]
    prior = meta.get("prior_month")
    if prior is None and len(months) >= 2:
        prior = months[-2]
    if prior is None:
        raise ValueError("Need at least two months to compute MoM metrics")

    return list(months), latest, prior


# ─────────────────────────────────────────────────────────────
# Main calculation
# ─────────────────────────────────────────────────────────────

def calculate_all(raw: dict) -> dict[str, Any]:
    cc = raw["credit_check"]
    disb = raw["disbursed"]
    meta = raw.get("metadata") or {}

    months, latest, prior = _resolve_months(raw)
    labels = {m: month_label(m) for m in months}

    # ── 1. Total counts per month ──────────────────────────
    status_counts = cc["status_counts"]
    total_count = {}
    for m in months:
        total_count[m] = status_counts["All"][m]

    # ── 2. Pipeline rates ──────────────────────────────────
    def rate(status, month):
        cnt = status_counts.get(status, {}).get(month, 0) or 0
        tot = total_count[month] or 0
        if tot == 0:
            return 0.0
        return cnt / tot * 100

    DR_current = rate("Disbursed", latest)
    DR_prior = rate("Disbursed", prior)
    RR_current = rate("Rejected", latest)
    RR_prior = rate("Rejected", prior)
    CA_current = rate("CA But LPS Not Done", latest)
    CA_prior = rate("CA But LPS Not Done", prior)

    DR_delta = round(DR_current - DR_prior, 2)
    RR_delta = round(RR_current - RR_prior, 2)
    DR_drop = DR_prior - DR_current  # positive if fell

    DR_current_r = round(DR_current, 2)
    DR_prior_r = round(DR_prior, 2)
    RR_current_r = round(RR_current, 2)
    RR_prior_r = round(RR_prior, 2)
    CA_current_r = round(CA_current, 2)
    CA_prior_r = round(CA_prior, 2)

    all_statuses = [s for s in status_counts.keys() if s != "All"]
    pipeline_funnel = []
    for st in all_statuses:
        row = {
            "status": st,
            "months": {},
            "mom_delta_pp": None,
            "direction": "informational",
        }
        for m in months:
            cnt = status_counts[st].get(m, 0) or 0
            tot = total_count[m] or 0
            pct = round(cnt / tot * 100, 2) if tot > 0 else 0.0
            row["months"][m] = {"count": cnt, "pct": pct}

        pct_cur = row["months"][latest]["pct"]
        pct_pri = row["months"][prior]["pct"]
        row["mom_delta_pp"] = round(pct_cur - pct_pri, 2)

        if st == "Disbursed":
            row["direction"] = "desirable_increase"
        elif st == "Rejected":
            row["direction"] = "desirable_decrease"
        else:
            row["direction"] = "informational"
        pipeline_funnel.append(row)

    # ── 3. Average Ticket Size ─────────────────────────────
    disbursal_summary = disb["disbursal_summary"]
    avg_ticket_per_month = {}
    for m in months:
        amt_cr = disbursal_summary["amount_cr"].get(m)
        cnt = disbursal_summary["size"].get(m)
        if cnt is None or amt_cr is None or cnt == 0:
            avg_ticket_per_month[m] = None
            continue
        avg_lakh = (amt_cr * 100) / cnt
        avg_lakh_r = round(avg_lakh, 2)
        sanity = round(avg_lakh_r * cnt / 100, 4)
        if abs(sanity - amt_cr) > 0.05:
            raise ValueError(
                f"Avg ticket sanity check failed for {m}: "
                f"sanity={sanity} vs amount_cr={amt_cr}"
            )
        if avg_lakh_r > 5.0:
            raise ValueError(
                f"Avg ticket > 5L for {m} ({avg_lakh_r}): wrong multiplier?"
            )
        avg_ticket_per_month[m] = avg_lakh_r

    avg_ticket_latest = avg_ticket_per_month[latest]

    # ── 4. PSI history ─────────────────────────────────────
    psi_cc_by_month = cc["psi_overall"]["psi"]
    psi_disb_by_month = disb["psi_disb"]["psi"]

    psi_history = []
    for m in months:
        psi_cc_val = psi_cc_by_month.get(m)
        psi_disb_val = psi_disb_by_month.get(m)
        psi_history.append({
            "month": labels[m],
            "month_key": m,
            "cc_psi": psi_cc_val,
            "disbursed_psi": psi_disb_val,
            "disbursed_rag": rag_psi_disbursed(psi_disb_val),
        })

    psi_cc_current = psi_cc_by_month.get(latest)
    psi_disb_current = psi_disb_by_month.get(latest)

    # ── 5. CSI feature analysis ────────────────────────────
    features_disb_csi = disb["features_psi_disb"]
    features_cc_csi = cc["features_psi"]

    all_feature_names = list(features_disb_csi.keys())

    features_out = []
    csi_unstable_count = 0
    csi_marginal_count = 0
    csi_stable_count = 0

    for fname in all_feature_names:
        disb_vals = features_disb_csi.get(fname, {})
        cc_vals = features_cc_csi.get(fname, {})

        disb_csi_all = {}
        cc_csi_all = {}
        disb_status_all = {}
        unstable_months = []

        for m in months:
            dv = disb_vals.get(m)
            cv = cc_vals.get(m)
            disb_csi_all[m] = dv
            cc_csi_all[m] = cv
            st = classify_csi(dv)
            disb_status_all[m] = st
            if st == "Unstable":
                unstable_months.append(labels[m])

        disb_csi_current = disb_csi_all[latest]
        cc_csi_current = cc_csi_all[latest]
        latest_status = disb_status_all[latest]

        if latest_status == "Unstable":
            csi_unstable_count += 1
        elif latest_status == "Marginal":
            csi_marginal_count += 1
        else:
            csi_stable_count += 1

        disb_csi_prior = disb_csi_all[prior]
        if disb_csi_current is not None and disb_csi_prior is not None:
            mom_delta = round(disb_csi_current - disb_csi_prior, 2)
        else:
            mom_delta = None

        streak = 0
        for m in reversed(months):
            val = disb_csi_all.get(m)
            if val is not None and val > 0.25:
                streak += 1
            else:
                break

        if disb_csi_current is not None and cc_csi_current is not None:
            adverse_selection = disb_csi_current > cc_csi_current
            csi_delta_cc_vs_disb = round(disb_csi_current - cc_csi_current, 2)
        else:
            adverse_selection = False
            csi_delta_cc_vs_disb = None

        features_out.append({
            "name": fname,
            "cc_csi": cc_csi_all,
            "disbursed_csi": disb_csi_all,
            "disbursed_status_latest": latest_status,
            "disbursed_status_all_months": disb_status_all,
            "mom_delta": mom_delta,
            "consecutive_instability_streak": streak,
            "unstable_months": unstable_months,
            "adverse_selection": adverse_selection,
            "csi_delta_cc_vs_disb": csi_delta_cc_vs_disb,
        })

    total_features = len(all_feature_names)
    assert csi_unstable_count + csi_marginal_count + csi_stable_count == total_features, \
        f"CSI counts don't sum to {total_features}"

    # ── 6. Top 3 ranking ───────────────────────────────────
    sorted_features = sorted(
        features_out,
        key=lambda f: f["disbursed_csi"][latest] or 0,
        reverse=True,
    )
    top_3_unstable = []
    for rank, feat in enumerate(sorted_features[:3], start=1):
        top_3_unstable.append({
            "rank": rank,
            "name": feat["name"],
            "disbursed_csi": feat["disbursed_csi"][latest],
            "status": feat["disbursed_status_latest"],
            "adverse_selection": feat["adverse_selection"],
        })

    # ── 7. Score bucket gap ────────────────────────────────
    cc_buckets = cc["v3_bucket_percentages"]
    disb_buckets = disb["v3_bucket_percentages"]
    all_buckets = [b for b in cc_buckets.keys() if b != "All"]

    score_buckets = []
    for bucket in all_buckets:
        cc_pct = cc_buckets[bucket].get(latest, 0.0) or 0.0
        disb_pct = (disb_buckets.get(bucket) or {}).get(latest, 0.0) or 0.0
        gap_pp = round(disb_pct - cc_pct, 2)
        absent_in_disbursed = (disb_pct == 0.0 and cc_pct > 0)
        score_buckets.append({
            "bucket": bucket,
            "cc_pct": cc_pct,
            "disbursed_pct": disb_pct,
            "gap_pp": gap_pp,
            "absent_in_disbursed": absent_in_disbursed,
        })

    # ── 8. Risk segment gap + ABSENT flag ─────────────────
    cc_seg_pct = cc["risk_segment_percentages"]
    disb_seg_pct = disb["risk_segment_percentages"]
    cc_segments = [s for s in cc_seg_pct.keys() if s != "All"]

    risk_segments = []
    for seg in cc_segments:
        cc_pct = cc_seg_pct[seg].get(latest)
        disb_pct = (disb_seg_pct.get(seg) or {}).get(latest)
        absent = (seg not in disb_seg_pct or disb_pct is None)
        if not absent and cc_pct is not None and disb_pct is not None:
            gap_pp = round(disb_pct - cc_pct, 2)
        else:
            gap_pp = None
        risk_segments.append({
            "segment": seg,
            "cc_pct": cc_pct,
            "disbursed_pct": disb_pct if not absent else 0.0,
            "gap_pp": gap_pp,
            "absent_in_disbursed": absent,
        })

    # ── 9. Approval method gap + Critical flag ─────────────
    cc_am_pct = cc["approval_method_percentages"]
    disb_am_pct = disb["approval_method_percentages"]
    all_methods = list(cc_am_pct.keys())

    approval_methods = []
    for method in all_methods:
        if method == "All":
            continue
        cc_pct = cc_am_pct[method].get(latest, 0.0) or 0.0
        disb_pct = (disb_am_pct.get(method) or {}).get(latest, 0.0) or 0.0
        gap_pp = round(disb_pct - cc_pct, 2)
        critical = (cc_pct > 20 and disb_pct < 5)
        approval_methods.append({
            "method": method,
            "cc_pct": cc_pct,
            "disbursed_pct": disb_pct,
            "gap_pp": gap_pp,
            "critical_conversion_flag": critical,
        })

    # ── 10. Bureau Depth segment gap + MoM delta ──────────
    cc_bd_pct = cc["bureau_depth_percentages"]
    disb_bd_pct = disb["bureau_depth_percentages"]
    bd_segments = [s for s in cc_bd_pct.keys() if s != "All"]

    bureau_depth_segments = []
    for seg in bd_segments:
        cc_pct_cur = cc_bd_pct[seg].get(latest)
        disb_pct_cur = (disb_bd_pct.get(seg) or {}).get(latest)
        disb_pct_pri = (disb_bd_pct.get(seg) or {}).get(prior)

        if disb_pct_cur is not None and cc_pct_cur is not None:
            gap_pp = round(disb_pct_cur - cc_pct_cur, 2)
        else:
            gap_pp = None

        if disb_pct_cur is not None and disb_pct_pri is not None:
            mom_delta = round(disb_pct_cur - disb_pct_pri, 2)
        else:
            mom_delta = None

        alert = (abs(mom_delta) > 3) if mom_delta is not None else False

        bureau_depth_segments.append({
            "segment": seg,
            "cc_pct": cc_pct_cur,
            "disbursed_pct": disb_pct_cur,
            "gap_pp": gap_pp,
            "disbursed_mom_delta_pp": mom_delta,
            "alert": alert,
        })

    # ── 11. Disbursal trend — Double Risk Event ───────────
    disbursal_trend = []
    for i, m in enumerate(months):
        cnt_raw = disbursal_summary["size"].get(m)
        amt_cr = disbursal_summary["amount_cr"].get(m)
        avg_tk = avg_ticket_per_month[m]
        dr_pct = round(rate("Disbursed", m), 2)
        cnt = int(cnt_raw) if cnt_raw is not None else 0

        if i == 0 or amt_cr is None:
            vol_delta = None
            amt_delta = None
            dre = False
        else:
            prev_m = months[i - 1]
            prev_cnt_raw = disbursal_summary["size"].get(prev_m)
            prev_amt = disbursal_summary["amount_cr"].get(prev_m)
            prev_cnt = int(prev_cnt_raw) if prev_cnt_raw is not None else 0
            vol_delta = cnt - prev_cnt
            amt_delta = round(amt_cr - prev_amt, 4) if prev_amt is not None else None
            dre = (vol_delta < 0 and amt_delta is not None and amt_delta < 0)

        disbursal_trend.append({
            "month": labels[m],
            "month_key": m,
            "disbursed_count": cnt,
            "amount_cr": amt_cr,
            "avg_ticket_lakhs": avg_tk,
            "disbursal_rate_pct": dr_pct,
            "vol_mom_delta": vol_delta,
            "amt_mom_delta": amt_delta,
            "double_risk_event": dre,
        })

    # ── 12. RAG badges ────────────────────────────────────
    rag_psi_disb = rag_psi_disbursed(psi_disb_current)
    rag_dr = rag_disbursal_rate(DR_drop)
    rag_rr = rag_rejection_rate(RR_delta)
    rag_ca = rag_ca_lps(CA_current)
    rag_csi = rag_feature_csi(csi_unstable_count, csi_marginal_count)

    # ── 13. Overall RAG ────────────────────────────────────
    overall_rag = worst_rag(
        rag_psi_disb,
        rag_csi,
        rag_dr,
        rag_rr,
        rag_ca,
    )

    # ── 14. Next review date ───────────────────────────────
    next_review_str = next_review_date_str(latest)

    # ── 15. Alerts list ────────────────────────────────────
    alerts = []

    if psi_disb_current is not None:
        if psi_disb_current > 0.20:
            alerts.append({
                "metric": "PSI (Disbursed)",
                "current_value": psi_disb_current,
                "threshold": "> 0.20",
                "status": "Critical",
                "persistent": False,
                "recommended_action": (
                    "Investigate distribution shift in disbursed population vs training baseline"
                ),
            })
        elif psi_disb_current >= 0.10:
            alerts.append({
                "metric": "PSI (Disbursed)",
                "current_value": psi_disb_current,
                "threshold": "0.10–0.20",
                "status": "Warning",
                "persistent": False,
                "recommended_action": (
                    "Monitor closely; consider retraining if trend continues"
                ),
            })

    for feat in features_out:
        latest_st = feat["disbursed_status_latest"]
        disb_val = feat["disbursed_csi"][latest]
        streak = feat["consecutive_instability_streak"]
        if latest_st == "Unstable":
            persistent = streak >= 2
            status_str = "Critical (Persistent)" if persistent else "Critical"
            alerts.append({
                "metric": f"Feature CSI: {feat['name']}",
                "current_value": disb_val,
                "threshold": "> 0.25 (Unstable)",
                "status": status_str,
                "persistent": persistent,
                "recommended_action": (
                    "Investigate feature data pipeline and population shift; flag for model review"
                ),
            })
        elif latest_st == "Marginal":
            alerts.append({
                "metric": f"Feature CSI: {feat['name']}",
                "current_value": disb_val,
                "threshold": "0.10–0.25 (Marginal)",
                "status": "Warning",
                "persistent": False,
                "recommended_action": (
                    "Monitor feature distribution; prepare contingency if shift continues"
                ),
            })

    if CA_current > 30:
        alerts.append({
            "metric": "CA But LPS Not Done %",
            "current_value": f"{CA_current_r}%",
            "threshold": "> 30%",
            "status": "Critical",
            "persistent": False,
            "recommended_action": "Immediate LPS process audit; identify bottleneck",
        })
    elif CA_current >= 15:
        alerts.append({
            "metric": "CA But LPS Not Done %",
            "current_value": f"{CA_current_r}%",
            "threshold": "15–30%",
            "status": "Warning",
            "persistent": False,
            "recommended_action": "Review LPS conversion process; identify friction points",
        })

    if DR_drop > 5:
        alerts.append({
            "metric": "Disbursal Rate Drop",
            "current_value": f"{round(DR_drop, 2)}pp drop",
            "threshold": "> 5pp",
            "status": "Critical",
            "persistent": False,
            "recommended_action": "Urgent review of disbursement pipeline and credit policy",
        })
    elif DR_drop > 3:
        alerts.append({
            "metric": "Disbursal Rate Drop",
            "current_value": f"{round(DR_drop, 2)}pp drop",
            "threshold": "> 3pp",
            "status": "Warning",
            "persistent": False,
            "recommended_action": "Monitor disbursal rate; review conversion funnel",
        })

    if RR_delta > 3:
        alerts.append({
            "metric": "Rejection Rate Delta",
            "current_value": f"+{RR_delta}pp",
            "threshold": "> +3pp",
            "status": "Warning",
            "persistent": False,
            "recommended_action": (
                "Review rejection reasons; check for policy tightening side-effects"
            ),
        })

    for am in approval_methods:
        if am["critical_conversion_flag"]:
            alerts.append({
                "metric": f"Approval Method Conversion: {am['method']}",
                "current_value": f"CC={am['cc_pct']}% → Disbursed={am['disbursed_pct']}%",
                "threshold": "CC > 20% AND Disbursed < 5%",
                "status": "Critical",
                "persistent": False,
                "recommended_action": (
                    f"Investigate why {am['method']} applications are not disbursing; "
                    "policy or process barrier"
                ),
            })

    for seg in risk_segments:
        if seg["absent_in_disbursed"] and seg["cc_pct"] and seg["cc_pct"] > 0:
            alerts.append({
                "metric": f"Risk Segment Absent: {seg['segment']}",
                "current_value": f"CC={seg['cc_pct']}% but 0% in Disbursed",
                "threshold": "Absent in Disbursed",
                "status": "Warning",
                "persistent": False,
                "recommended_action": (
                    f"Investigate why {seg['segment']} segment is not converting to disbursals"
                ),
            })

    for bucket in score_buckets:
        if bucket["absent_in_disbursed"]:
            alerts.append({
                "metric": f"Score Bucket Absent: {bucket['bucket']}",
                "current_value": f"CC={bucket['cc_pct']}% but 0% in Disbursed",
                "threshold": "Absent in Disbursed",
                "status": "Warning",
                "persistent": False,
                "recommended_action": (
                    f"Investigate score bucket {bucket['bucket']} conversion gap"
                ),
            })

    for bd in bureau_depth_segments:
        if bd["alert"]:
            alerts.append({
                "metric": f"Bureau Depth MoM Shift: {bd['segment']}",
                "current_value": f"{bd['disbursed_mom_delta_pp']}pp MoM",
                "threshold": "|MoM delta| > 3pp",
                "status": "Warning",
                "persistent": False,
                "recommended_action": (
                    f"Monitor bureau depth segment {bd['segment']} for continued shift"
                ),
            })

    # ── 16. Assemble output ────────────────────────────────
    total_cc_latest = total_count[latest]
    size_latest = disbursal_summary["size"].get(latest)
    total_disb_latest = int(size_latest) if size_latest is not None else 0

    report_date_raw = meta.get("report_date") or ""
    report_date_display = format_report_date(report_date_raw) if report_date_raw else ""

    result = {
        "metadata": {
            "model_name": meta.get("model_name") or "Uptop V3",
            "purpose": meta.get("purpose") or "Risk Model — Bajaj Credit Check Stage",
            "reporting_month": reporting_month_label(latest),
            "reporting_month_key": latest,
            "prior_month_key": prior,
            "report_date": report_date_display or report_date_raw,
            "monitoring_cadence": meta.get("monitoring_cadence") or "Monthly",
            "next_review_date": next_review_str,
            "months_available": [labels[m] for m in months],
            "months_available_keys": months,
            "total_cc_applications_latest": total_cc_latest,
            "total_disbursals_latest": total_disb_latest,
            "source_file": meta.get("source_file") or "",
        },
        "overall_rag": overall_rag,
        "kpis": {
            "psi_credit_check": {
                "value": psi_cc_current,
                "rag": "Informational",
            },
            "psi_disbursed": {
                "value": psi_disb_current,
                "rag": rag_psi_disb,
            },
            "disbursal_rate": {
                "current_pct": DR_current_r,
                "prior_pct": DR_prior_r,
                "delta_pp": DR_delta,
                "rag": rag_dr,
            },
            "rejection_rate": {
                "current_pct": RR_current_r,
                "prior_pct": RR_prior_r,
                "delta_pp": RR_delta,
                "rag": rag_rr,
            },
            "ca_lps_not_done_pct": {
                "current_pct": CA_current_r,
                "prior_pct": CA_prior_r,
                "delta_pp": round(CA_current - CA_prior, 2),
                "rag": rag_ca,
            },
            "avg_ticket_lakhs": {
                "value": avg_ticket_latest,
                "rag": "Informational",
            },
            "feature_csi_summary": {
                "unstable": csi_unstable_count,
                "marginal": csi_marginal_count,
                "stable": csi_stable_count,
                "rag": rag_csi,
            },
        },
        "pipeline_funnel": pipeline_funnel,
        "psi_history": psi_history,
        "score_buckets": score_buckets,
        "features": features_out,
        "top_3_unstable": top_3_unstable,
        "disbursal_trend": disbursal_trend,
        "risk_segments": risk_segments,
        "approval_methods": approval_methods,
        "bureau_depth_segments": bureau_depth_segments,
        "alerts": alerts,
    }

    return result


def run(input_path: str, output_path: str) -> dict:
    raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
    result = calculate_all(raw)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Written: {output_path}")

    kpis = result["kpis"]
    print(f"Overall RAG: {result['overall_rag']}")
    print(
        f"DR: {kpis['disbursal_rate']['current_pct']}%  "
        f"delta={kpis['disbursal_rate']['delta_pp']}pp  "
        f"({kpis['disbursal_rate']['rag']})"
    )
    print(
        f"RR: {kpis['rejection_rate']['current_pct']}%  "
        f"delta={kpis['rejection_rate']['delta_pp']}pp  "
        f"({kpis['rejection_rate']['rag']})"
    )
    print(
        f"CA-LPS: {kpis['ca_lps_not_done_pct']['current_pct']}%  "
        f"delta={kpis['ca_lps_not_done_pct']['delta_pp']}pp  "
        f"({kpis['ca_lps_not_done_pct']['rag']})"
    )
    print(f"Avg Ticket: {kpis['avg_ticket_lakhs']['value']} L")
    fsum = kpis["feature_csi_summary"]
    print(
        f"CSI: {fsum['unstable']}U / {fsum['marginal']}M / "
        f"{fsum['stable']}S ({fsum['rag']})"
    )
    print(
        f"PSI (Disbursed): {kpis['psi_disbursed']['value']}  "
        f"({kpis['psi_disbursed']['rag']})"
    )
    print(
        f"PSI (CC): {kpis['psi_credit_check']['value']}  "
        f"({kpis['psi_credit_check']['rag']})"
    )
    print(f"Next Review: {result['metadata']['next_review_date']}")
    print(f"Alerts: {len(result['alerts'])}")
    top3_str = [(t["name"], t["disbursed_csi"]) for t in result["top_3_unstable"]]
    print(f"Top 3: {top3_str}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="data/uptop-v3-mmr/extracted_data.json",
    )
    parser.add_argument(
        "--output",
        default="data/uptop-v3-mmr/mmr_calculated.json",
    )
    args = parser.parse_args()
    run(args.input, args.output)
