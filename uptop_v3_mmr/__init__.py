"""UpTop V3 MMR pipeline: HTML extract → raw JSON → calculated MMR JSON."""

__all__ = [
    "extract_raw",
    "extract_raw_from_html",
    "calculate_all",
]


def __getattr__(name):
    if name in ("extract_raw", "extract_raw_from_html"):
        from uptop_v3_mmr.extractor import extract_raw, extract_raw_from_html
        return {"extract_raw": extract_raw, "extract_raw_from_html": extract_raw_from_html}[name]
    if name == "calculate_all":
        from uptop_v3_mmr.mmr_calculator import calculate_all
        return calculate_all
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
