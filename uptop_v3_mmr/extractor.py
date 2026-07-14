#!/usr/bin/env python3
"""
UpTop V3 Model Monitoring HTML → raw table JSON extractor.

Parses the monitoring HTML and emits the raw schema expected by mmr_calculator:
  { metadata, credit_check, disbursed }

Usage:
    python3 -m uptop_v3_mmr.extractor <html_file_path> [output_json_path]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional, Union

from bs4 import BeautifulSoup

EXTRACTOR_VERSION = "1.1.0"
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


# ---------------------------------------------------------------------------
# HTML Parsing Helpers
# ---------------------------------------------------------------------------

def build_table_index(soup):
    """Walk DOM in document order; return (section, h3_title, table) tuples."""
    index = []
    current_section = None
    current_h3 = None

    for element in soup.find_all(["h2", "h3", "table"]):
        tag = element.name
        if tag == "h2":
            current_section = element.get_text(strip=True)
            current_h3 = None
        elif tag == "h3":
            current_h3 = element.get_text(strip=True)
        elif tag == "table":
            index.append((current_section, current_h3, element))

    return index


def parse_dataframe_table(table_el):
    """Parse class=dataframe table with dual-row thead → {row: {month: value}}."""
    thead = table_el.find("thead")
    thead_rows = thead.find_all("tr")

    first_row_cells = thead_rows[0].find_all(["th", "td"])
    columns = [c.get_text(strip=True) for c in first_row_cells[1:]]

    result = {}
    tbody = table_el.find("tbody")
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row_label = cells[0].get_text(strip=True)
        row_data = {}
        for col, cell in zip(columns, cells[1:]):
            text = cell.get_text(strip=True)
            try:
                val = float(text)
            except ValueError:
                val = text if text else None
            row_data[col] = val
        result[row_label] = row_data

    return result


def parse_styled_table(table_el):
    """Parse styled id=T_* table → {row: {month: value}}."""
    thead = table_el.find("thead")
    thead_cells = thead.find_all(["th", "td"])
    columns = [c.get_text(strip=True) for c in thead_cells[1:]]

    result = {}
    tbody = table_el.find("tbody")
    for tr in tbody.find_all("tr"):
        th = tr.find("th", class_=re.compile(r"row_heading"))
        if th is None:
            continue
        row_label = th.get_text(strip=True)
        data_cells = tr.find_all("td", class_=re.compile(r"data"))
        row_data = {}
        for col, cell in zip(columns, data_cells):
            text = cell.get_text(strip=True)
            try:
                val = float(text)
            except ValueError:
                val = text if text else None
            row_data[col] = val
        result[row_label] = row_data

    return result


def auto_parse_table(table_el):
    classes = table_el.get("class", [])
    tid = table_el.get("id", "")
    if "dataframe" in classes:
        return parse_dataframe_table(table_el)
    if tid.startswith("T_"):
        return parse_styled_table(table_el)
    return parse_dataframe_table(table_el)


def _discover_months(*tables) -> list:
    months = set()
    for tbl in tables:
        if not tbl:
            continue
        for row_data in tbl.values():
            for col in row_data.keys():
                if MONTH_RE.match(str(col)):
                    months.add(col)
    return sorted(months)


def _report_date_from_name(name: str) -> str:
    """
    Parse report date from filename.
    Supports:
      - MonDDMMYYYY  e.g. Mon22062026 → 2026-06-22
      - YYYYMMDD     e.g. 20260622    → 2026-06-22
    """
    name = name or ""
    mon_match = re.search(r"Mon(\d{2})(\d{2})(\d{4})", name, re.IGNORECASE)
    if mon_match:
        dd, mm, yyyy = mon_match.groups()
        return f"{yyyy}-{mm}-{dd}"

    date_match = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if date_match:
        yyyy, mm, dd = date_match.groups()
        return f"{yyyy}-{mm}-{dd}"

    return ""


def _transpose_metric_row(parsed: dict, row_key: str) -> dict:
    """Extract a single metric row as {month: value}."""
    return dict(parsed.get(row_key, {}) or {})


def _disbursal_summary(dis_data: dict) -> dict:
    """Build size / amount / amount_cr maps from the disbursal dataframe."""
    summary = {}
    for key in ("size", "amount", "amount_cr"):
        summary[key] = dict(dis_data.get(key, {}) or {})
    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_raw_from_html(
    html: Union[str, bytes],
    *,
    source_name: str = "",
    source_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    Parse UpTop V3 monitoring HTML into the raw schema for mmr_calculator.

    Table positions (document order, 0-based):
      Credit Check 0–11, Disbursed 12–22 (see module docstring in original extractor).
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8")

    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    report_title = title_tag.get_text(strip=True) if title_tag else ""
    h1_tag = soup.find("h1")
    if h1_tag:
        report_title = h1_tag.get_text(strip=True)

    table_index = build_table_index(soup)
    if len(table_index) < 23:
        raise RuntimeError(
            f"Expected ≥23 tables in UpTop V3 HTML, found {len(table_index)}"
        )

    def get_table(pos: int) -> dict:
        _, _, tbl = table_index[pos]
        return auto_parse_table(tbl)

    cc_status = get_table(0)
    cc_status_pct = get_table(1)
    cc_v3_pct = get_table(3)
    psi_overall = get_table(4)
    features_cc = get_table(5)
    cc_risk_pct = get_table(7)
    cc_approval_pct = get_table(9)
    cc_bureau_pct = get_table(11)

    dis_data = get_table(12)
    dis_v3_pct = get_table(14)
    psi_disb = get_table(15)
    features_disb = get_table(16)
    dis_risk_pct = get_table(18)
    dis_approval_pct = get_table(20)
    dis_bureau_pct = get_table(22)

    all_months = _discover_months(cc_status, dis_data, psi_overall, psi_disb)
    if not all_months:
        raise RuntimeError("No YYYY-MM month columns found in HTML tables")

    latest_month = all_months[-1]
    prior_month = all_months[-2] if len(all_months) >= 2 else None
    report_date = _report_date_from_name(source_name or (source_path or ""))

    raw = {
        "metadata": {
            "extractor_version": EXTRACTOR_VERSION,
            "model_name": "Uptop V3",
            "purpose": "Risk Model — Bajaj Credit Check Stage",
            "report_title": report_title,
            "report_date": report_date,
            "source_file": source_path or source_name or "",
            "months": all_months,
            "latest_month": latest_month,
            "prior_month": prior_month,
            "monitoring_cadence": "Monthly",
        },
        "credit_check": {
            "status_counts": cc_status,
            "status_percentages": cc_status_pct,
            "v3_bucket_percentages": cc_v3_pct,
            "psi_overall": {"psi": _transpose_metric_row(psi_overall, "psi")},
            "features_psi": features_cc,
            "risk_segment_percentages": cc_risk_pct,
            "approval_method_percentages": cc_approval_pct,
            "bureau_depth_percentages": cc_bureau_pct,
        },
        "disbursed": {
            "disbursal_summary": _disbursal_summary(dis_data),
            "v3_bucket_percentages": dis_v3_pct,
            "psi_disb": {"psi": _transpose_metric_row(psi_disb, "psi")},
            "features_psi_disb": features_disb,
            "risk_segment_percentages": dis_risk_pct,
            "approval_method_percentages": dis_approval_pct,
            "bureau_depth_percentages": dis_bureau_pct,
        },
    }
    return raw


def extract_raw(html_path: Union[str, Path], output_path: Optional[Union[str, Path]] = None) -> dict:
    """Read HTML from disk, extract raw JSON, optionally write to output_path."""
    html_path = Path(html_path).resolve()
    if not html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {html_path}")

    html = html_path.read_text(encoding="utf-8")
    raw = extract_raw_from_html(
        html,
        source_name=html_path.name,
        source_path=str(html_path),
    )

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Raw extraction complete. Output: {output_path}")

    return raw


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 -m uptop_v3_mmr.extractor <html_path> [output_json_path]")
        sys.exit(1)

    html_file = sys.argv[1]
    out_file = sys.argv[2] if len(sys.argv) > 2 else str(
        Path(html_file).parent / "extracted_data.json"
    )
    extract_raw(html_file, out_file)
