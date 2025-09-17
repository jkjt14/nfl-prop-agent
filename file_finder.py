# file_finder.py
from __future__ import annotations
import re, glob, os
from typing import Optional, Tuple

_PATTERN = re.compile(r"raw_stats_(\d{4})_wk(\d{1,2})\.csv$", re.IGNORECASE)

def parse_year_week(filename: str) -> Optional[Tuple[int, int]]:
    """Return (year, week) parsed from raw_stats_YYYY_wkN.csv, else None."""
    base = os.path.basename(filename)
    m = _PATTERN.search(base)
    if not m:
        return None
    year = int(m.group(1))
    week = int(m.group(2))
    return year, week

def find_latest_raw_stats(data_dir: str = "data") -> Optional[Tuple[str, int, int]]:
    """
    Find newest raw_stats_YYYY_wkN.csv by (year, week). Returns (path, year, week)
    or None if nothing matches.
    """
    candidates = glob.glob(os.path.join(data_dir, "raw_stats_*_wk*.csv"))
    best = None  # (year, week, path)
    for p in candidates:
        yw = parse_year_week(p)
        if not yw:
            continue
        year, week = yw
        if (best is None) or (year, week) > (best[0], best[1]):
            best = (year, week, p)
    if best is None:
        return None
    return best[2], best[0], best[1]

def resolve_projection_path(preferred: Optional[str] = None) -> Tuple[str, Optional[int], Optional[int]]:
    """
    If preferred path is given and exists, use it.
    Otherwise pick latest raw_stats_YYYY_wkN.csv in data/.
    Returns (path, year, week)
    """
    if preferred and os.path.exists(preferred):
        yw = parse_year_week(preferred)
        if yw:
            return preferred, yw[0], yw[1]
        return preferred, None, None
    found = find_latest_raw_stats("data")
    if not found:
        raise FileNotFoundError(
            "No projections file found. Provide PROJECTIONS_PATH or place a file like "
            "data/raw_stats_2025_wk3.csv."
        )
    return found
