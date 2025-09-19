"""Command line interface for the NFL prop agent.

This CLI loads projection data, runs the edge scanner, writes artifacts,
and optionally posts Slack alerts.  Configuration is pulled from
``agent_config.yaml`` via ``config.load_config`` to keep settings
consistent across entry points.  It also normalizes projection column
names and computes derived statistics (such as pass+rush yards) so
that the scanning logic can operate on a consistent set of markets.
"""

from __future__ import annotations

import logging
import os
import sys
import pandas as pd

import logging
logging.basicConfig(level=logging.INFO)

from agent_core import scan_edges
from alerts import alert_edges
from config import load_config, validate_target_books
from cleaning import clean_projections
from file_finder import resolve_projection_path  # NEW
from market_utils import resolve_market_column

def load_projections(path: str) -> pd.DataFrame:
    """Load projection CSV, clean it and normalize columns for markets."""
    df = pd.read_csv(path)
    df = clean_projections(df)
    logging.info(
        "Loaded/cleaned projections: %s rows, %s cols from %s",
        len(df),
        len(df.columns),
        path,
    )
    return df

def projection_health_summary(df: pd.DataFrame, markets: list[str]) -> list[dict]:
    """Return coverage stats for requested markets."""
    total = int(len(df))
    summary: list[dict] = []
    if not markets:
        return summary
    for market in markets:
        entry = {"market": market, "total": total}
        col = resolve_market_column(df.columns, market)
        if not col:
            entry["status"] = "missing_column"
        else:
            missing = int(df[col].isna().sum())
            entry.update({
                "status": "ok",
                "missing": missing,
                "available": total - missing,
                "column": col,
            })
        summary.append(entry)
    return summary


def format_projection_health(summary: list[dict]) -> list[str]:
    """Format projection health stats for logging/artifacts."""
    lines: list[str] = []
    for entry in summary:
        market = entry.get("market")
        total = entry.get("total", 0)
        status = entry.get("status")
        if status == "missing_column":
            lines.append(f"{market}: column missing ({total} players affected)")
        else:
            available = entry.get("available", 0)
            missing = entry.get("missing", 0)
            pct = (available / total * 100) if total else 0.0
            column = entry.get("column", market)
            suffix = f" (column {column})" if column and column != market else ""
            lines.append(
                f"{market}{suffix}: {available}/{total} projections ({pct:.1f}% coverage, missing {missing})"
            )
    return lines


def format_scan_diagnostics(diag: dict, reason_limit: int = 10) -> list[str]:
    """Return formatted diagnostic lines from a scan."""
    if not diag:
        return []
    lines: list[str] = []
    events = diag.get("events")
    events_used = diag.get("events_used")
    if events is not None and events_used is not None:
        lines.append(f"Events processed: {events_used}/{events}")
    est = diag.get("estimated_credits")
    if est is not None:
        lines.append(f"Estimated credits: {est}")
    markets_trimmed = diag.get("markets_trimmed")
    if markets_trimmed:
        lines.append("Markets trimmed for budget: " + ", ".join(markets_trimmed))
    markets_effective = diag.get("markets_effective")
    if markets_effective:
        lines.append("Markets used: " + ", ".join(markets_effective))
    target_books = diag.get("target_books")
    if target_books:
        lines.append("Target books: " + ", ".join(target_books))
    seen = diag.get("bookmakers_encountered") or []
    if seen:
        lines.append("Bookmakers encountered: " + ", ".join(seen))
    offers_by_book = diag.get("offers_by_book") or {}
    if offers_by_book:
        parts = [f"{book}={count}" for book, count in sorted(offers_by_book.items(), key=lambda kv: (-kv[1], kv[0]))]
        lines.append("Offers by target book: " + ", ".join(parts))
    fallback_counts = diag.get("fallback_counts") or {}
    if fallback_counts:
        parts = [
            f"{book}={count}"
            for book, count in sorted(fallback_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        lines.append("Fallback outside target books: " + ", ".join(parts))
    missing_events = diag.get("events_missing_bookmakers") or []
    if missing_events:
        sample = ", ".join(str(ev.get("event_id")) for ev in missing_events[:5])
        lines.append(f"Events missing bookmakers: {len(missing_events)} (sample {sample})")
    reasons = diag.get("reasons") or {}
    if reasons:
        lines.append("Top skip reasons:")
        for reason, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:reason_limit]:
            lines.append(f"  {reason}: {count}")
    missing_proj = diag.get("missing_projection_values") or {}
    if missing_proj:
        lines.append("Missing projection counts:")
        for market, count in sorted(missing_proj.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {market}: {count}")
    return lines


def advice_lines(df: pd.DataFrame, threshold: float) -> str:
    """Format human-readable advice lines for Slack/console."""
    if df is None or df.empty:
        return "No edges found."
    name_map = {
        "player_pass_yds": "passing yards",
        "player_pass_yards": "passing yards",
        "player_rush_yds": "rushing yards",
        "player_rush_yards": "rushing yards",
        "player_reception_yds": "receiving yards",
        "player_receiving_yards": "receiving yards",
        "player_receptions": "receptions",
        "player_pass_yds": "passing yards",
        "player_pass_tds": "pass TDs",
        "player_pass_touchdowns": "pass TDs",
        "player_pass_longest_completion": "longest completion",
        "player_pass_rush_yds": "pass+rush yards",
        "player_pass_rush_reception_yds": "pass+rush+rec yards",
        "player_pass_rush_reception_tds": "pass+rush+rec TDs",
        "player_rush_tds": "rush TDs",
        "player_rush_touchdowns": "rush TDs",
        "player_rush_attempts": "rush attempts",
        "player_reception_tds": "rec TDs",
        "player_receiving_touchdowns": "rec TDs",
        "player_interceptions": "def INTs",
        "player_pass_interceptions": "pass INTs",
        "player_pass_completions": "pass completions",
        "player_pass_attempts": "pass attempts",
        "player_longest_reception": "longest reception",
        "player_reception_longest": "longest reception",
        "player_longest_rush": "longest rush",
        "player_rush_longest": "longest rush",
    }
    keep = df[df["ev_per_unit"] >= threshold].copy()
    if keep.empty:
        return "No edges ≥ threshold."
    lines = []
    for _, r in keep.head(50).iterrows():
        evp = f"{r['ev_per_unit']*100:.1f}%"
        fallback_note = ""
        fb_book = r.get("fallback_book")
        if fb_book and isinstance(fb_book, str):
            fb_line = r.get("fallback_line")
            fb_odds = r.get("fallback_odds")
            if pd.notna(fb_book):
                line_str = "NA" if pd.isna(fb_line) else f"{fb_line:g}" if isinstance(fb_line, (int, float)) else str(fb_line)
                odds_str = "NA" if pd.isna(fb_odds) else str(int(fb_odds))
                fallback_note = f" (alt: {fb_book} {odds_str} @ {line_str})"
        lines.append(
            f"{r['player']} {r['side']} {r['book_line']} {name_map.get(r['market_key'], r['market_key'])} — "
            f"{r['book_odds']} ({r['best_book']}) | EV {evp} | {r['stake_u']}u{fallback_note}"
        )
    return "\n".join(lines)

def main() -> int:
    logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))

    # Prefer env var; else auto-pick latest raw_stats_YYYY_wkN.csv in data/
    pref = os.environ.get("PROJECTIONS_PATH", "").strip() or None
    try:
        proj_path, year, week = resolve_projection_path(pref)
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        return 1
    if year and week:
        logging.info("Using projections file for %d week %d: %s", year, week, proj_path)
    else:
        logging.info("Using projections file: %s", proj_path)

    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        logging.error("Missing ODDS_API_KEY.")
        return 2

    threshold = float(os.environ.get("EDGE_THRESHOLD", "0.06"))
    profile = os.environ.get("MARKETS_PROFILE", "base")

    cfg = load_config()
    markets_for_profile = cfg.get("markets", {}).get(profile, cfg.get("markets", {}).get("base", []))
    if markets_for_profile:
        logging.info("Markets for profile '%s': %s", profile, markets_for_profile)
    else:
        logging.warning("No markets configured for profile '%s'.", profile)

    book_check = validate_target_books(cfg.get("target_books", []))
    if book_check["unknown"]:
        logging.warning("Unknown target_books keys: %s", ", ".join(book_check["unknown"]))
        for book, suggestions in book_check["suggestions"].items():
            logging.warning("  %s → possible matches: %s", book, ", ".join(suggestions))
    elif not cfg.get("target_books"):
        logging.info("No target_books configured; scan will consider all bookmakers.")

    df_proj = load_projections(proj_path)
    proj_summary = projection_health_summary(df_proj, list(markets_for_profile or []))
    for entry in proj_summary:
        status = entry.get("status")
        market = entry.get("market")
        if status == "missing_column":
            logging.warning("Projection column missing for %s (profile=%s)", market, profile)
        else:
            missing = entry.get("missing", 0)
            if missing > 0:
                available = entry.get("available", 0)
                total = entry.get("total", 0)
                pct = (available / total * 100) if total else 0.0
                logging.info(
                    "Projection coverage for %s: %d/%d (%.1f%%); missing=%d",
                    market,
                    available,
                    total,
                    pct,
                    missing,
                )
    logging.info("LOG_MARKETS_ONCE = %s", os.environ.get("LOG_MARKETS_ONCE"))


    
    # Determine how far ahead to look for events.  Default to 2 days, but allow
    # override via the DAYS_FROM environment variable.  In practice, player
    # prop lines are often posted only a couple of days before kickoff, so
    # limiting the lookahead period reduces "no_bookmakers" events.
    days_from_env = os.environ.get("DAYS_FROM", "2").strip()
    try:
        days_from = int(days_from_env)
    except Exception:
        days_from = 2

    df_edges = scan_edges(
        df_proj,
        cfg,
        api_key=api_key,
        days_from=days_from,
        profile=profile,
        max_calls=1000,
    )

    diag = {}
    if isinstance(df_edges, pd.DataFrame):
        diag = df_edges.attrs.get("diagnostics", {})
    diag_lines = format_scan_diagnostics(diag)
    proj_lines = format_projection_health(proj_summary)
    if diag_lines:
        logging.info("\n=== SCAN DIAGNOSTICS ===\n%s\n", "\n".join(diag_lines))
    if any(entry.get("status") == "missing_column" or entry.get("missing", 0) > 0 for entry in proj_summary):
        logging.info("Projection coverage summary:\n%s", "\n".join(proj_lines))

    os.makedirs("artifacts", exist_ok=True)
    if df_edges is not None and not df_edges.empty:
        df_edges.to_csv("artifacts/edges.csv", index=False)

    if proj_lines:
        with open("artifacts/projection_health.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(proj_lines) + "\n")
    if diag_lines:
        with open("artifacts/diagnostics.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(diag_lines) + "\n")

    adv = advice_lines(df_edges, threshold)
    with open("artifacts/advice.txt", "w", encoding="utf-8") as f:
        f.write(adv + "\n")

    logging.info("\n=== ADVICE ===\n%s\n", adv)
    alert_edges(df_edges, threshold_ev=threshold)
    return 0

if __name__ == "__main__":
    sys.exit(main())
