#!/usr/bin/env python3
"""
UpTop V3 Model Monitoring HTML Report Extractor
Version: 2.0.0

Pure raw-data extractor — no computed metrics, no derived rates,
no RAG flags, no thresholds, no alerts. The AI receiving this JSON
will do all calculations itself.

Used by uptop_v3.py to convert the S3 HTML report into structured JSON
in-memory before it is uploaded to Toqan as an attachment (Toqan analyzes
the JSON instead of the raw HTML for this model).

Standalone CLI usage (unchanged from the original script):
    python3 -m utils.uptop_v3_extractor <html_file_path> [output_json_path]
"""

import sys
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

EXTRACTOR_VERSION = "2.0.0"

# Number of tables the extractor expects to find, in document order, before
# it can safely index into them (see EXPECTED_TABLE_LAYOUT below).
_EXPECTED_TABLE_COUNT = 23


# ---------------------------------------------------------------------------
# HTML Parsing Helpers
# ---------------------------------------------------------------------------

def build_table_index(soup):
    """
    Walk the DOM in document order, tracking h2 section and h3 title.
    Returns a list of (section, h3_title, table_element) tuples in document order.
    """
    index = []
    current_section = None
    current_h3 = None

    for element in soup.find_all(['h2', 'h3', 'table']):
        tag = element.name
        if tag == 'h2':
            current_section = element.get_text(strip=True)
            current_h3 = None
        elif tag == 'h3':
            current_h3 = element.get_text(strip=True)
        elif tag == 'table':
            index.append((current_section, current_h3, element))

    return index


def parse_dataframe_table(table_el):
    """
    Parse a class="dataframe" table with dual-row thead.
    Returns dict: { row_label: { month: value, ... }, ... }
    The first thead row holds column headers; second row holds the pivot label name.
    Row labels are in <th> cells of tbody rows.
    """
    thead = table_el.find('thead')
    thead_rows = thead.find_all('tr')

    # First thead row: skip first cell (row-label column header), rest are months/cols
    first_row_cells = thead_rows[0].find_all(['th', 'td'])
    columns = [c.get_text(strip=True) for c in first_row_cells[1:]]

    result = {}
    tbody = table_el.find('tbody')
    for tr in tbody.find_all('tr'):
        cells = tr.find_all(['th', 'td'])
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
    """
    Parse a styled table with id="T_..." — single-row thead with month columns.
    Row labels in <th class="row_heading ..."> in tbody.
    Values in <td class="data ..."> cells.
    Returns dict: { row_label: { month: value, ... }, ... }
    """
    thead = table_el.find('thead')
    thead_cells = thead.find_all(['th', 'td'])
    # First cell is the index label (e.g. "credit_check_month" or ""), skip it
    columns = [c.get_text(strip=True) for c in thead_cells[1:]]

    result = {}
    tbody = table_el.find('tbody')
    for tr in tbody.find_all('tr'):
        # Row heading
        th = tr.find('th', class_=re.compile(r'row_heading'))
        if th is None:
            continue
        row_label = th.get_text(strip=True)
        # Data cells
        data_cells = tr.find_all('td', class_=re.compile(r'data'))
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
    """Auto-detect and parse based on table attributes."""
    classes = table_el.get('class', [])
    tid = table_el.get('id', '')
    if 'dataframe' in classes:
        return parse_dataframe_table(table_el)
    elif tid.startswith('T_'):
        return parse_styled_table(table_el)
    else:
        return parse_dataframe_table(table_el)


def get_months_from_table(parsed_table):
    """Extract all YYYY-MM formatted month keys from a parsed table's first row."""
    months = set()
    pattern = re.compile(r'^\d{4}-\d{2}$')
    for row_data in parsed_table.values():
        for col in row_data.keys():
            if pattern.match(str(col)):
                months.add(col)
    return sorted(months)


# ---------------------------------------------------------------------------
# Main Extractor
# ---------------------------------------------------------------------------

_MONTH_NAMES = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
    "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
    "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


def _month_name(mm):
    return _MONTH_NAMES.get(mm, mm)


def _parse_report_date(digits):
    """
    Best-effort human-readable date from an 8-digit filename fragment.

    Real filenames use DDMMYYYY (e.g. "...Mon22062026.html" -> 22 Jun 2026),
    so that's tried first; falls back to YYYYMMDD in case a differently
    named report is ever used. Returns the raw digits if neither
    interpretation yields a valid month, rather than fabricating a date.
    """
    dd, mm, yyyy = digits[0:2], digits[2:4], digits[4:8]
    if mm in _MONTH_NAMES:
        return f"{dd} {_MONTH_NAMES[mm]} {yyyy}"

    yyyy2, mm2, dd2 = digits[0:4], digits[4:6], digits[6:8]
    if mm2 in _MONTH_NAMES:
        return f"{dd2} {_MONTH_NAMES[mm2]} {yyyy2}"

    return digits


def extract_from_html(html, source_label=""):
    """
    Parse UpTop V3 report HTML (as a string) into the raw-data JSON structure.

    Args:
        html:         Full HTML document as a string.
        source_label: Human-readable label for the source (e.g. original
                       filename) — used only for `_meta.source_file` and to
                       detect a report date embedded in the filename.

    Returns:
        dict: the extracted JSON structure (see module docstring).

    Raises:
        RuntimeError: if the HTML doesn't contain the expected table layout.
    """
    soup = BeautifulSoup(html, 'lxml')

    # Extract report metadata
    title_tag = soup.find('title')
    report_title = title_tag.get_text(strip=True) if title_tag else ""
    h1_tag = soup.find('h1')
    if h1_tag:
        report_title = h1_tag.get_text(strip=True)

    # Build document-order table index
    table_index = build_table_index(soup)

    if len(table_index) < _EXPECTED_TABLE_COUNT:
        raise RuntimeError(
            f"UpTop V3 extractor: expected at least {_EXPECTED_TABLE_COUNT} tables "
            f"in the report HTML, found {len(table_index)}. The report layout may "
            f"have changed — extraction aborted rather than risk silently dropping data."
        )

    # Assign tables by position (0-based):
    # Credit Check Data (0-11):
    #  0: Current Status by CC Month          (dataframe) — counts
    #  1: Current Status % by CC Month        (dataframe) — percentages
    #  2: V3 Bucket by CC Month               (dataframe) — counts
    #  3: V3 Bucket % by CC Month             (dataframe) — percentages
    #  4: PSI Overall                         (styled)
    #  5: Features Overall                    (styled)
    #  6: Risk Segment by CC Month            (dataframe) — counts
    #  7: Risk Segment % by CC Month          (dataframe) — percentages
    #  8: Approval Method by CC Month         (dataframe) — counts
    #  9: Approval Method % by CC Month       (dataframe) — percentages
    # 10: Bureau Depth by CC Month            (dataframe) — counts
    # 11: Bureau Depth % by CC Month          (dataframe) — percentages
    # Disbursed Data (12-22):
    # 12: Disbursal Data                      (dataframe) — size, amount, amount_cr
    # 13: V3 Bucket disb                      (dataframe) — counts
    # 14: V3 Bucket % disb                    (dataframe) — percentages
    # 15: PSI disb                            (styled)
    # 16: Features disb                       (styled)
    # 17: Risk Segment disb                   (dataframe) — counts
    # 18: Risk Segment % disb                 (dataframe) — percentages
    # 19: Approval Method disb                (dataframe) — counts
    # 20: Approval Method % disb              (dataframe) — percentages
    # 21: Bureau Depth disb                   (dataframe) — counts
    # 22: Bureau Depth % disb                 (dataframe) — percentages

    def get_table(pos):
        _, _, tbl = table_index[pos]
        return auto_parse_table(tbl)

    # Parse all tables
    cc_status        = get_table(0)
    cc_status_pct    = get_table(1)
    cc_v3_bucket     = get_table(2)
    cc_v3_pct        = get_table(3)
    psi_overall      = get_table(4)
    features_cc      = get_table(5)
    cc_risk_seg      = get_table(6)
    cc_risk_pct      = get_table(7)
    cc_approval      = get_table(8)
    cc_approval_pct  = get_table(9)
    cc_bureau_depth  = get_table(10)
    cc_bureau_pct    = get_table(11)

    dis_data         = get_table(12)
    dis_v3_bucket    = get_table(13)
    dis_v3_pct       = get_table(14)
    psi_disb         = get_table(15)
    features_disb    = get_table(16)
    dis_risk_seg     = get_table(17)
    dis_risk_pct     = get_table(18)
    dis_approval     = get_table(19)
    dis_approval_pct = get_table(20)
    dis_bureau_depth = get_table(21)
    dis_bureau_pct   = get_table(22)

    # Discover all months dynamically
    month_pattern = re.compile(r'^\d{4}-\d{2}$')
    all_months = set()
    for tbl in [cc_status, dis_data, psi_overall, psi_disb]:
        for row_data in tbl.values():
            for col in row_data.keys():
                if month_pattern.match(str(col)):
                    all_months.add(col)
    all_months = sorted(all_months)
    latest_month = max(all_months) if all_months else None
    prior_month = sorted(all_months)[-2] if len(all_months) >= 2 else None

    # Detect report date from the source label / filename
    report_date = ""
    date_match = re.search(r'(\d{8})', str(source_label))
    if date_match:
        report_date = _parse_report_date(date_match.group(1))

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def flat_month_dict(parsed_table, row_key):
        """Return {month: value} for the given row_key, month cols only."""
        row = parsed_table.get(row_key, {})
        return {
            col: val
            for col, val in row.items()
            if month_pattern.match(str(col))
        }

    def month_cols_only(row_dict):
        """Return a copy of row_dict keeping only YYYY-MM keys."""
        return {k: v for k, v in row_dict.items() if month_pattern.match(str(k))}

    def build_counts_pct(count_table, pct_table, align_rows=None):
        """
        Build {counts: {row: {col: val}}, percentages: {row: {month: val}}}.

        counts:      All columns preserved as-is (including 'All' where present).
        percentages: Month columns only. Rows absent from pct_table are included
                     with {month: None} — explicit nulls rather than silent gaps.

        align_rows:  Optional iterable of row labels to ensure are present
                     (filled with nulls when missing from this table).
        """
        all_rows = list(count_table.keys())
        for row in pct_table.keys():
            if row not in all_rows:
                all_rows.append(row)
        if align_rows:
            for row in align_rows:
                if row not in all_rows:
                    all_rows.append(row)

        counts = {}
        for row in all_rows:
            row_data = count_table.get(row, {})
            counts[row] = dict(row_data) if row_data else {}

        null_months = {m: None for m in all_months}
        percentages = {}
        for row in all_rows:
            if row == "All":
                continue
            row_data = pct_table.get(row, {})
            month_data = month_cols_only(row_data)
            if month_data:
                percentages[row] = month_data
            else:
                percentages[row] = dict(null_months)

        return {"counts": counts, "percentages": percentages}

    def build_feature_csi(features_table):
        """Return {feature_name: {month: value}}."""
        result = {}
        for feat, row_data in features_table.items():
            result[feat] = month_cols_only(row_data)
        return result

    # -----------------------------------------------------------------------
    # Alignment row sets: ensures both CC and disbursed sections share the
    # same row labels (missing ones appear as null rather than being absent).
    # -----------------------------------------------------------------------
    cc_approval_rows  = [r for r in cc_approval.keys() if r != "All"]
    dis_approval_rows = [r for r in dis_approval.keys() if r != "All"]
    all_approval_rows = list(dict.fromkeys(cc_approval_rows + dis_approval_rows))

    cc_risk_rows  = [r for r in cc_risk_seg.keys() if r != "All"]
    dis_risk_rows = [r for r in dis_risk_seg.keys() if r != "All"]
    all_risk_rows = list(dict.fromkeys(cc_risk_rows + dis_risk_rows))

    cc_bureau_rows  = [r for r in cc_bureau_depth.keys() if r != "All"]
    dis_bureau_rows = [r for r in dis_bureau_depth.keys() if r != "All"]
    all_bureau_rows = list(dict.fromkeys(cc_bureau_rows + dis_bureau_rows))

    cc_status_rows = list(cc_status.keys())
    cc_v3_rows  = list(cc_v3_bucket.keys())
    dis_v3_rows = list(dis_v3_bucket.keys())
    all_v3_rows = list(dict.fromkeys(cc_v3_rows + dis_v3_rows))

    # PSI — flat {month: value}
    cc_psi  = flat_month_dict(psi_overall, "psi")
    dis_psi = flat_month_dict(psi_disb, "psi")

    # Disbursal data rows — month cols only
    dis_size_dict      = month_cols_only(dis_data.get("size", {}))
    dis_amount_dict    = month_cols_only(dis_data.get("amount", {}))
    dis_amount_cr_dict = month_cols_only(dis_data.get("amount_cr", {}))

    # -----------------------------------------------------------------------
    # Assemble output
    # -----------------------------------------------------------------------
    output = {
        "_meta": {
            "extractor_version":   EXTRACTOR_VERSION,
            "report_title":        report_title,
            "report_date":         report_date,
            "source_file":         str(source_label),
            "all_months_detected": all_months,
            "latest_month":        latest_month,
            "prior_month":         prior_month,
        },

        "credit_check": {
            "application_status":    build_counts_pct(cc_status, cc_status_pct,
                                                      align_rows=cc_status_rows),
            "v3_score_buckets":      build_counts_pct(cc_v3_bucket, cc_v3_pct,
                                                      align_rows=all_v3_rows),
            "psi":                   cc_psi,
            "feature_csi":           build_feature_csi(features_cc),
            "risk_segments":         build_counts_pct(cc_risk_seg, cc_risk_pct,
                                                      align_rows=all_risk_rows),
            "approval_methods":      build_counts_pct(cc_approval, cc_approval_pct,
                                                      align_rows=all_approval_rows),
            "bureau_depth_segments": build_counts_pct(cc_bureau_depth, cc_bureau_pct,
                                                      align_rows=all_bureau_rows),
        },

        "disbursed": {
            "disbursal_data": {
                "size":      dis_size_dict,
                "amount":    dis_amount_dict,
                "amount_cr": dis_amount_cr_dict,
            },
            "v3_score_buckets":      build_counts_pct(dis_v3_bucket, dis_v3_pct,
                                                      align_rows=all_v3_rows),
            "psi":                   dis_psi,
            "feature_csi":           build_feature_csi(features_disb),
            "risk_segments":         build_counts_pct(dis_risk_seg, dis_risk_pct,
                                                      align_rows=all_risk_rows),
            "approval_methods":      build_counts_pct(dis_approval, dis_approval_pct,
                                                      align_rows=all_approval_rows),
            "bureau_depth_segments": build_counts_pct(dis_bureau_depth, dis_bureau_pct,
                                                      align_rows=all_bureau_rows),
        },
    }

    return output


# ---------------------------------------------------------------------------
# Prompt-injection helper — exact category labels for this file
# ---------------------------------------------------------------------------

def build_label_whitelist(data):
    """
    Build a plain-text block listing the exact category label strings found
    in this specific extracted file (score buckets, risk segments, approval
    methods, feature names, application statuses).

    This is injected into the LLM prompt (replacing the {{VALID_LABELS}}
    marker in the uptop_v3 prompt) so the model cannot substitute a
    generic industry-standard scheme (CIBIL score bands, VL/L/M/H/VH risk
    tiers, manual/auto/rejected approval flags) for this model's actual,
    non-standard labels — a failure mode observed in production where the
    LLM fell back on familiar "textbook" categories instead of reading the
    file's real keys.
    """

    def _labels(section, field):
        counts = data.get(section, {}).get(field, {}).get("counts", {})
        return [k for k in counts.keys() if k != "All"]

    def _union(field):
        cc = _labels("credit_check", field)
        dis = _labels("disbursed", field)
        seen = []
        for lbl in cc + dis:
            if lbl not in seen:
                seen.append(lbl)
        return seen

    v3_buckets = _union("v3_score_buckets")
    risk_segments = _union("risk_segments")
    approval_methods = _union("approval_methods")
    application_statuses = _labels("credit_check", "application_status")

    feature_names = []
    for section in ("credit_check", "disbursed"):
        for feat in data.get(section, {}).get("feature_csi", {}).keys():
            if feat not in feature_names:
                feature_names.append(feat)

    lines = []
    lines.append(f"- Application status categories: {', '.join(application_statuses)}")
    lines.append(f"- V3 score bucket labels: {', '.join(v3_buckets)}")
    lines.append(f"- Risk segment labels: {', '.join(risk_segments)}")
    lines.append(f"- Approval method labels: {', '.join(approval_methods)}")
    lines.append(f"- Feature names (for CSI table): {', '.join(feature_names)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI (standalone) wrapper — reads/writes files on disk
# ---------------------------------------------------------------------------

def extract(html_path, output_path):
    html_path = Path(html_path).resolve()
    if not html_path.exists():
        print(f"ERROR: HTML file not found: {html_path}", file=sys.stderr)
        sys.exit(1)

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    output = extract_from_html(html, source_label=str(html_path))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Extraction complete. Output: {output_path}")
    return output


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <html_path> [output_json_path]")
        sys.exit(1)

    html_file = sys.argv[1]
    out_file  = sys.argv[2] if len(sys.argv) > 2 else str(
        Path(html_file).parent / "extracted.json"
    )
    extract(html_file, out_file)
