#!/usr/bin/env python3
"""
UpTop V3 — deterministic metrics computation.

Root cause of the hallucination problem this file exists to fix: asking an
LLM to simultaneously (a) verbatim-retrieve 100+ precise numbers from a
dense JSON file and (b) compose a long, richly formatted HTML report proved
unreliable even after prompt-level anti-fabrication rules and an exact
category-label whitelist were added — those fixes drove label/category
hallucination to zero, but had no effect on numeric hallucination (every
PSI, CSI, count, and rate was still invented in testing).

This module removes the LLM from the numeric-retrieval path entirely: every
KPI, RAG status, table row, and alert in the report is computed here in
plain Python directly from the extractor's output (the same JSON that was
previously just handed to the LLM and hoped-for). The LLM (see
uptop_v3.py::generate_narrative) is only ever asked to write prose sentences
around numbers it is handed already-computed — it never has to look anything
up, so it can no longer invent a PSI value, a feature name, or a count.
"""

# ---------------------------------------------------------------------------
# Thresholds — mirrored 1:1 from the (now-retired) LLM prompt's Early Warning
# / RAG rules, so behaviour doesn't silently change when moving from
# "the LLM applies these thresholds" to "Python applies these thresholds".
# ---------------------------------------------------------------------------

PSI_DISBURSED_AMBER = 0.10
PSI_DISBURSED_RED = 0.20

CSI_DISBURSED_AMBER = 0.10
CSI_DISBURSED_RED = 0.25

CA_LPS_GREEN_MAX = 15.0
CA_LPS_RED_MIN = 30.0

REJECTION_RATE_AMBER_MOM_PP = 3.0

DISBURSAL_RATE_AMBER_MOM_DROP_PP = 3.0
DISBURSAL_RATE_RED_MOM_DROP_PP = 5.0

APPROVAL_GAP_CC_SHARE_MIN = 20.0
APPROVAL_GAP_DISBURSED_SHARE_MAX = 5.0

RAG_RANK = {"Green": 0, "Amber": 1, "Red": 2}


def _worst_rag(*statuses):
    ranked = [s for s in statuses if s in RAG_RANK]
    if not ranked:
        return "Green"
    return max(ranked, key=lambda s: RAG_RANK[s])


def _round(value, ndigits=2):
    if value is None:
        return None
    return round(value, ndigits)


def _pct(numerator, denominator, ndigits=2):
    if numerator is None or denominator in (None, 0):
        return None
    return _round(100.0 * numerator / denominator, ndigits)


def _get_month(d, month):
    if not d or month is None:
        return None
    return d.get(month)


def _recent_months(all_months, n=3):
    return all_months[-n:] if all_months else []


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_kpi_summary(data, latest, prior):
    cc_psi = _get_month(data["credit_check"]["psi"], latest)
    dis_psi = _get_month(data["disbursed"]["psi"], latest)
    dis_psi_rag = (
        "Red" if dis_psi is not None and dis_psi > PSI_DISBURSED_RED else
        "Amber" if dis_psi is not None and dis_psi >= PSI_DISBURSED_AMBER else
        "Green" if dis_psi is not None else None
    )

    cc_status_counts = data["credit_check"]["application_status"]["counts"]
    total_latest = _get_month(cc_status_counts.get("All", {}), latest)
    total_prior = _get_month(cc_status_counts.get("All", {}), prior)
    disbursed_latest = _get_month(cc_status_counts.get("Disbursed", {}), latest)
    disbursed_prior = _get_month(cc_status_counts.get("Disbursed", {}), prior)
    rejected_latest = _get_month(cc_status_counts.get("Rejected", {}), latest)
    rejected_prior = _get_month(cc_status_counts.get("Rejected", {}), prior)
    ca_lps_latest = _get_month(cc_status_counts.get("CA But LPS Not Done", {}), latest)
    ca_lps_prior = _get_month(cc_status_counts.get("CA But LPS Not Done", {}), prior)

    disbursal_rate = _pct(disbursed_latest, total_latest)
    disbursal_rate_prior = _pct(disbursed_prior, total_prior)
    disbursal_mom = (
        _round(disbursal_rate - disbursal_rate_prior)
        if disbursal_rate is not None and disbursal_rate_prior is not None else None
    )
    disbursal_rag = (
        "Red" if disbursal_mom is not None and disbursal_mom <= -DISBURSAL_RATE_RED_MOM_DROP_PP else
        "Amber" if disbursal_mom is not None and disbursal_mom <= -DISBURSAL_RATE_AMBER_MOM_DROP_PP else
        "Green" if disbursal_mom is not None else None
    )

    rejection_rate = _pct(rejected_latest, total_latest)
    rejection_rate_prior = _pct(rejected_prior, total_prior)
    rejection_mom = (
        _round(rejection_rate - rejection_rate_prior)
        if rejection_rate is not None and rejection_rate_prior is not None else None
    )
    rejection_rag = (
        "Amber" if rejection_mom is not None and rejection_mom > REJECTION_RATE_AMBER_MOM_PP else
        "Green" if rejection_mom is not None else None
    )

    ca_lps_pct = _pct(ca_lps_latest, total_latest)
    ca_lps_pct_prior = _pct(ca_lps_prior, total_prior)
    ca_lps_mom = (
        _round(ca_lps_pct - ca_lps_pct_prior)
        if ca_lps_pct is not None and ca_lps_pct_prior is not None else None
    )
    ca_lps_rag = (
        "Red" if ca_lps_pct is not None and ca_lps_pct > CA_LPS_RED_MIN else
        "Amber" if ca_lps_pct is not None and ca_lps_pct >= CA_LPS_GREEN_MAX else
        "Green" if ca_lps_pct is not None else None
    )

    dis_size = _get_month(data["disbursed"]["disbursal_data"]["size"], latest)
    dis_amount_cr = _get_month(data["disbursed"]["disbursal_data"]["amount_cr"], latest)
    avg_ticket_lakhs = (
        _round(dis_amount_cr * 100.0 / dis_size, 2)
        if dis_amount_cr is not None and dis_size not in (None, 0) else None
    )

    csi_counts, csi_rag = _feature_csi_counts_and_rag(data, latest)

    overall_rag = _worst_rag(dis_psi_rag, csi_rag, ca_lps_rag, disbursal_rag, rejection_rag)

    return {
        "psi_credit_check": {"value": cc_psi, "rag": "Informational"},
        "psi_disbursed": {"value": dis_psi, "rag": dis_psi_rag},
        "disbursal_rate": {"value": disbursal_rate, "mom_delta_pp": disbursal_mom, "rag": disbursal_rag},
        "rejection_rate": {"value": rejection_rate, "mom_delta_pp": rejection_mom, "rag": rejection_rag},
        "ca_lps_pct": {"value": ca_lps_pct, "mom_delta_pp": ca_lps_mom, "rag": ca_lps_rag},
        "avg_ticket_size_lakhs": {"value": avg_ticket_lakhs, "rag": None},
        "feature_csi_counts": {**csi_counts, "rag": csi_rag},
        "overall_rag": overall_rag,
    }


def _feature_csi_counts_and_rag(data, month):
    unstable = marginal = stable = 0
    for _feat, monthly in data["disbursed"]["feature_csi"].items():
        val = monthly.get(month)
        if val is None:
            continue
        if val > CSI_DISBURSED_RED:
            unstable += 1
        elif val >= CSI_DISBURSED_AMBER:
            marginal += 1
        else:
            stable += 1
    rag = "Red" if unstable > 0 else "Amber" if marginal > 0 else "Green"
    return {"unstable": unstable, "marginal": marginal, "stable": stable}, rag


def _build_funnel(data, months):
    counts = data["credit_check"]["application_status"]["counts"]
    statuses = [s for s in counts.keys() if s != "All"]
    # Keep a stable, sensible display order matching the original prompt spec.
    preferred_order = ["Disbursed", "Rejected", "CA But LPS Not Done", "Pre-approved",
                        "Expired", "Open", "Cancelled", "Closed"]
    statuses = [s for s in preferred_order if s in statuses] + \
               [s for s in statuses if s not in preferred_order]

    rows = []
    for status in statuses:
        row_counts = counts.get(status, {})
        totals = counts.get("All", {})
        by_month = {}
        pct_by_month = {}
        for m in months:
            c = _get_month(row_counts, m)
            t = _get_month(totals, m)
            by_month[m] = c
            pct_by_month[m] = _pct(c, t)

        mom_delta_pp = None
        if len(months) >= 2:
            latest_pct, prior_pct = pct_by_month.get(months[-1]), pct_by_month.get(months[-2])
            if latest_pct is not None and prior_pct is not None:
                mom_delta_pp = _round(latest_pct - prior_pct)

        # "Desirable direction" is status-dependent: growth is good for
        # Disbursed/Pre-approved, bad for Rejected/CA-LPS/Expired/Cancelled.
        undesirable_if_up = status in ("Rejected", "CA But LPS Not Done", "Expired", "Cancelled")
        delta_color = None
        if mom_delta_pp is not None and mom_delta_pp != 0:
            went_up = mom_delta_pp > 0
            is_bad = went_up if undesirable_if_up else not went_up
            delta_color = "red" if is_bad else "green"

        rows.append({
            "status": status,
            "counts": by_month,
            "percentages": pct_by_month,
            "mom_delta_pp": mom_delta_pp,
            "delta_color": delta_color,
        })
    return rows


def _build_psi_table(data, months):
    rows = []
    for m in months:
        cc = _get_month(data["credit_check"]["psi"], m)
        dis = _get_month(data["disbursed"]["psi"], m)
        rag = (
            "Red" if dis is not None and dis > PSI_DISBURSED_RED else
            "Amber" if dis is not None and dis >= PSI_DISBURSED_AMBER else
            "Green" if dis is not None else None
        )
        rows.append({"month": m, "credit_check_psi": cc, "disbursed_psi": dis, "disbursed_rag": rag})
    return rows


def _effective_pct_and_gap(cc_v, dis_v, absent):
    """
    An "absent" category (e.g. STATED_INCOME had zero disbursed applications
    in every month) is a real, known fact from the source data — not a
    missing/unknown value — so it should read as a hard 0%, not "—". This
    matters: treating it as unknown (None) instead of 0 was letting the
    single most important real finding in the whole dataset (STATED_INCOME
    fully filtered out of disbursals) silently fail to trigger its alert.
    """
    if absent:
        effective_dis_v = 0.0
    else:
        effective_dis_v = dis_v
    gap = _round(effective_dis_v - cc_v) if effective_dis_v is not None and cc_v is not None else None
    return effective_dis_v, gap


def _build_score_buckets(data, latest):
    cc_pct = data["credit_check"]["v3_score_buckets"]["percentages"]
    dis_pct = data["disbursed"]["v3_score_buckets"]["percentages"]
    order = [b for b in cc_pct.keys()]

    rows = []
    for bucket in order:
        cc_v = _get_month(cc_pct.get(bucket, {}), latest)
        dis_v = _get_month(dis_pct.get(bucket, {}), latest)
        absent_in_disbursed = _is_absent(data["disbursed"], "v3_score_buckets", bucket)
        effective_dis_v, _gap = _effective_pct_and_gap(cc_v, dis_v, absent_in_disbursed)
        rows.append({
            "bucket": bucket,
            "credit_check_pct": cc_v,
            "disbursed_pct": effective_dis_v,
            "absent_in_disbursed": absent_in_disbursed,
        })
    return rows


def _build_csi_table(data, latest, all_months):
    cc_csi = data["credit_check"]["feature_csi"]
    dis_csi = data["disbursed"]["feature_csi"]
    rows = []
    for feature in dis_csi.keys():
        cc_v = _get_month(cc_csi.get(feature, {}), latest)
        dis_v = _get_month(dis_csi.get(feature, {}), latest)
        status = (
            "Unstable" if dis_v is not None and dis_v > CSI_DISBURSED_RED else
            "Marginal" if dis_v is not None and dis_v >= CSI_DISBURSED_AMBER else
            "Stable" if dis_v is not None else None
        )
        delta = _round(dis_v - cc_v) if dis_v is not None and cc_v is not None else None

        # 2+ consecutive months Unstable, ending at latest month.
        monthly = dis_csi.get(feature, {})
        consecutive_unstable = 0
        for m in reversed(all_months):
            v = monthly.get(m)
            if v is not None and v > CSI_DISBURSED_RED:
                consecutive_unstable += 1
            else:
                break
        multi_month_unstable = consecutive_unstable >= 2

        rows.append({
            "feature": feature,
            "credit_check_csi": cc_v,
            "disbursed_csi": dis_v,
            "disbursed_status": status,
            "delta": delta,
            "adverse_selection": bool(delta is not None and delta > 0),
            "multi_month_unstable": multi_month_unstable,
        })

    rows.sort(key=lambda r: (r["disbursed_csi"] is None, -(r["disbursed_csi"] or 0)))
    return rows


def _build_disbursal_table(data, months):
    size = data["disbursed"]["disbursal_data"]["size"]
    amount_cr = data["disbursed"]["disbursal_data"]["amount_cr"]
    cc_totals = data["credit_check"]["application_status"]["counts"].get("All", {})

    rows = []
    prev_count, prev_amount = None, None
    for m in months:
        count = _get_month(size, m)
        amt_cr = _get_month(amount_cr, m)
        total = _get_month(cc_totals, m)
        rate = _pct(count, total)
        avg_ticket = _round(amt_cr * 100.0 / count, 2) if amt_cr is not None and count not in (None, 0) else None

        double_risk = bool(
            prev_count is not None and prev_amount is not None and
            count is not None and amt_cr is not None and
            count < prev_count and amt_cr < prev_amount
        )

        rows.append({
            "month": m,
            "count": count,
            "amount_cr": amt_cr,
            "avg_ticket_lakhs": avg_ticket,
            "disbursal_rate_pct": rate,
            "double_risk_event": double_risk,
        })
        prev_count, prev_amount = count, amt_cr
    return rows


def _is_absent(section_data, field, label):
    row = section_data[field]["counts"].get(label, {})
    return not row or all(v in (None, 0) for k, v in row.items() if k != "All")


def _build_risk_segments(data, latest):
    cc_pct = data["credit_check"]["risk_segments"]["percentages"]
    dis_pct = data["disbursed"]["risk_segments"]["percentages"]
    rows = []
    for segment in cc_pct.keys():
        cc_v = _get_month(cc_pct.get(segment, {}), latest)
        dis_v = _get_month(dis_pct.get(segment, {}), latest)
        absent = _is_absent(data["disbursed"], "risk_segments", segment)
        effective_dis_v, gap = _effective_pct_and_gap(cc_v, dis_v, absent)
        rows.append({
            "segment": segment,
            "credit_check_pct": cc_v,
            "disbursed_pct": effective_dis_v,
            "gap_pp": gap,
            "absent_in_disbursed": absent,
        })
    return rows


def _build_approval_methods(data, latest):
    cc_pct = data["credit_check"]["approval_methods"]["percentages"]
    dis_pct = data["disbursed"]["approval_methods"]["percentages"]
    rows = []
    for method in cc_pct.keys():
        cc_v = _get_month(cc_pct.get(method, {}), latest)
        dis_v = _get_month(dis_pct.get(method, {}), latest)
        absent = _is_absent(data["disbursed"], "approval_methods", method)
        effective_dis_v, gap = _effective_pct_and_gap(cc_v, dis_v, absent)
        conversion_alert = bool(
            cc_v is not None and effective_dis_v is not None and
            cc_v > APPROVAL_GAP_CC_SHARE_MIN and effective_dis_v < APPROVAL_GAP_DISBURSED_SHARE_MAX
        )
        rows.append({
            "method": method,
            "credit_check_pct": cc_v,
            "disbursed_pct": effective_dis_v,
            "gap_pp": gap,
            "absent_in_disbursed": absent,
            "conversion_alert": conversion_alert,
        })
    return rows


def _build_alerts(kpi, csi_table, score_buckets, risk_segments, approval_methods):
    alerts = []

    dis_psi = kpi["psi_disbursed"]["value"]
    if dis_psi is not None:
        if dis_psi > PSI_DISBURSED_RED:
            alerts.append({"metric": "PSI (Disbursed)", "current_value": dis_psi,
                            "threshold": f"> {PSI_DISBURSED_RED}", "status": "Critical",
                            "recommended_action": "Investigate population drift; validate input data "
                                                   "sources and disbursal eligibility criteria"})
        elif dis_psi >= PSI_DISBURSED_AMBER:
            alerts.append({"metric": "PSI (Disbursed)", "current_value": dis_psi,
                            "threshold": f"{PSI_DISBURSED_AMBER}\u2013{PSI_DISBURSED_RED}", "status": "Warning",
                            "recommended_action": "Monitor population drift trend closely"})

    for row in csi_table:
        if row["disbursed_status"] == "Unstable":
            status = "High Priority" if row["multi_month_unstable"] else "Critical"
            alerts.append({"metric": f"{row['feature']} (CSI Disbursed)",
                            "current_value": row["disbursed_csi"],
                            "threshold": f"> {CSI_DISBURSED_RED}", "status": status,
                            "recommended_action": "Data quality audit; validate bureau data feed; "
                                                   "assess feature engineering stability"})
        elif row["disbursed_status"] == "Marginal":
            alerts.append({"metric": f"{row['feature']} (CSI Disbursed)",
                            "current_value": row["disbursed_csi"],
                            "threshold": f"{CSI_DISBURSED_AMBER}\u2013{CSI_DISBURSED_RED}", "status": "Warning",
                            "recommended_action": "Monitor for trend; validate feature distribution changes"})

    disbursal_rate = kpi["disbursal_rate"]
    if disbursal_rate["mom_delta_pp"] is not None:
        drop = -disbursal_rate["mom_delta_pp"]
        if drop > DISBURSAL_RATE_RED_MOM_DROP_PP:
            alerts.append({"metric": "Disbursal Rate MoM", "current_value": f"{disbursal_rate['value']}%",
                            "threshold": f"drop > {DISBURSAL_RATE_RED_MOM_DROP_PP}pp", "status": "Critical",
                            "recommended_action": "Investigate disbursal pipeline for process or eligibility issues"})
        elif drop > DISBURSAL_RATE_AMBER_MOM_DROP_PP:
            alerts.append({"metric": "Disbursal Rate MoM", "current_value": f"{disbursal_rate['value']}%",
                            "threshold": f"drop > {DISBURSAL_RATE_AMBER_MOM_DROP_PP}pp", "status": "Warning",
                            "recommended_action": "Monitor disbursal rate trend"})

    ca_lps = kpi["ca_lps_pct"]
    if ca_lps["value"] is not None:
        if ca_lps["value"] > CA_LPS_RED_MIN:
            alerts.append({"metric": "CA But LPS Not Done %", "current_value": f"{ca_lps['value']}%",
                            "threshold": f"> {CA_LPS_RED_MIN}%", "status": "Critical",
                            "recommended_action": "Audit post-approval pipeline; identify documentation/"
                                                   "verification bottlenecks; expedite LPS completion"})
        elif ca_lps["value"] >= CA_LPS_GREEN_MAX:
            alerts.append({"metric": "CA But LPS Not Done %", "current_value": f"{ca_lps['value']}%",
                            "threshold": f"{CA_LPS_GREEN_MAX}\u2013{CA_LPS_RED_MIN}%", "status": "Warning",
                            "recommended_action": "Monitor LPS completion trend"})

    rejection = kpi["rejection_rate"]
    if rejection["mom_delta_pp"] is not None and rejection["mom_delta_pp"] > REJECTION_RATE_AMBER_MOM_PP:
        alerts.append({"metric": "Rejection Rate MoM", "current_value": f"{rejection['value']}%",
                        "threshold": f"increase > {REJECTION_RATE_AMBER_MOM_PP}pp", "status": "Warning",
                        "recommended_action": "Review rejection reasons for the period"})

    for row in approval_methods:
        if row["conversion_alert"]:
            note = " — fully filtered out of disbursals" if row["absent_in_disbursed"] else ""
            alerts.append({"metric": f"Approval method: {row['method']}",
                            "current_value": f"CC {row['credit_check_pct']}% / Disbursed {row['disbursed_pct']}%{note}",
                            "threshold": f"CC > {APPROVAL_GAP_CC_SHARE_MIN}% & Disbursed < {APPROVAL_GAP_DISBURSED_SHARE_MAX}%",
                            "status": "Critical" if row["absent_in_disbursed"] else "Warning",
                            "recommended_action": "Investigate why this approval method converts poorly to disbursal"})

    for row in score_buckets:
        if row["absent_in_disbursed"] and (row["credit_check_pct"] or 0) > 0:
            alerts.append({"metric": f"V3 score bucket {row['bucket']}",
                            "current_value": f"CC {row['credit_check_pct']}% / Disbursed 0%",
                            "threshold": "present in CC, absent in Disbursed", "status": "Warning",
                            "recommended_action": "Confirm this is an intended hard cutoff, not a pipeline defect"})

    for row in risk_segments:
        if row["absent_in_disbursed"] and (row["credit_check_pct"] or 0) > 0:
            alerts.append({"metric": f"Risk segment {row['segment']}",
                            "current_value": f"CC {row['credit_check_pct']}% / Disbursed 0%",
                            "threshold": "present in CC, absent in Disbursed", "status": "Warning",
                            "recommended_action": "Confirm this is an intended hard cutoff, not a pipeline defect"})

    return alerts


def _next_review_date(latest_month):
    """2 weeks from the last day of the latest reporting month, e.g. '2026-05' -> 14 Jun 2026."""
    if not latest_month:
        return None
    import calendar
    from datetime import date, timedelta
    year, month = (int(x) for x in latest_month.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    review_date = date(year, month, last_day) + timedelta(days=14)
    return review_date.strftime("%d %b %Y")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_report_metrics(data):
    """
    Compute every KPI, RAG status, table row, and alert needed for the
    8-section UpTop V3 report, deterministically, straight from the
    extractor's output. Nothing in the returned dict is looked up or
    computed by an LLM — every value here is either copied directly from
    `data` or derived from it with plain arithmetic, so it is exactly as
    trustworthy as `data` itself (which extract_from_html() already
    guarantees matches the source HTML table-for-table).
    """
    meta = data.get("_meta", {})
    all_months = meta.get("all_months_detected") or []
    latest = meta.get("latest_month")
    prior = meta.get("prior_month")
    recent_months = _recent_months(all_months, 3)

    kpi = _build_kpi_summary(data, latest, prior)
    funnel = _build_funnel(data, recent_months)
    psi_table = _build_psi_table(data, recent_months)
    score_buckets = _build_score_buckets(data, latest)
    csi_table = _build_csi_table(data, latest, all_months)
    disbursal_table = _build_disbursal_table(data, recent_months)
    risk_segments = _build_risk_segments(data, latest)
    approval_methods = _build_approval_methods(data, latest)
    alerts = _build_alerts(kpi, csi_table, score_buckets, risk_segments, approval_methods)

    return {
        "meta": {
            "report_title": meta.get("report_title"),
            "report_date": meta.get("report_date"),
            "latest_month": latest,
            "prior_month": prior,
            "recent_months": recent_months,
            "all_months": all_months,
            "next_review_date": _next_review_date(latest),
        },
        "kpi": kpi,
        "overall_rag": kpi["overall_rag"],
        "funnel": funnel,
        "psi_table": psi_table,
        "score_buckets": score_buckets,
        "csi_table": csi_table,
        "top_unstable_features": [r for r in csi_table if r["disbursed_csi"] is not None][:3],
        "disbursal_table": disbursal_table,
        "risk_segments": risk_segments,
        "approval_methods": approval_methods,
        "alerts": alerts,
    }
