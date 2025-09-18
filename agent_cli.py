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
import numpy as np
import pandas as pd

from agent_core import scan_edges
from alerts import alert_edges
from config import load_config
from cleaning import clean_projections
from file_finder import resolve_projection_path  # NEW

# Mapping of projection stat aliases to Odds API market keys.
#
# This map serves two purposes: it allows the CLI to accept projection
# files with a variety of column names, and it defines the set of markets
# for which combo columns may be computed.  When adding new markets to
# your config, also add their aliases here.
STAT_TO_MARKET = {
    "pass_yds": "player_pass_yds",
    "pass_tds": "player_pass_tds",
    "pass_int": "player_pass_interceptions",
    "pass_attempts": "player_pass_attempts",
    "pass_att": "player_pass_attempts",
    "pass_comp": "player_pass_completions",
    "pass_completions": "player_pass_completions",
    "rush_yds": "player_rush_yds",
    "rush_tds": "player_rush_tds",
    "rush_att": "player_rush_attempts",
    "rush_attempts": "player_rush_attempts",
    "rec": "player_receptions",
    "receptions": "player_receptions",
    "rec_yds": "player_reception_yds",
    "rec_tds": "player_reception_tds",
    "pass_rush_yds": "player_pass_rush_yds",
    "pass_rush_rec_yds": "player_pass_rush_reception_yds",
    "pass_rush_rec_tds": "player_pass_rush_reception_tds",
}

def _ensure_market_columns(df: pd.DataFrame) -> None:
    """Backfill ``player_*`` market columns from common projection aliases.

    This function modifies ``df`` in-place.  It normalizes all alias
    columns defined in ``STAT_TO_MARKET``, creates corresponding ``player_*``
    columns where necessary, and computes combined stat columns when
    constituent columns exist.  A log message lists any markets that
    were created via alias mapping.
    """
    created: list[str] = []
    for alias, market in STAT_TO_MARKET.items():
        if market not in df.columns and alias in df.columns:
            df[market] = df[alias]
            created.append(market)
        sd_alias = f"{alias}_sd"
        sd_market = f"{market}_sd"
        if sd_market not in df.columns and sd_alias in df.columns:
            df[sd_market] = df[sd_alias]

    # Compute pass+rush+rec yards combo if not already present
    if "player_pass_rush_reception_yds" not in df.columns:
        comps = [c for c in ("pass_yds", "rush_yds", "rec_yds") if c in df.columns]
        if len(comps) == 3:
            df["player_pass_rush_reception_yds"] = df[comps].fillna(0).sum(axis=1)
            sd_cols = [f"{c}_sd" for c in comps if f"{c}_sd" in df.columns]
            if sd_cols:
                df["player_pass_rush_reception_yds_sd"] = np.sqrt(
                    df[sd_cols].pow(2).fillna(0).sum(axis=1)
                )

    # Compute pass+rush yards combo if not already present
    if "player_pass_rush_yds" not in df.columns:
        comps = [c for c in ("pass_yds", "rush_yds") if c in df.columns]
        if len(comps) == 2:
            df["player_pass_rush_yds"] = df[comps].fillna(0).sum(axis=1)
            sd_cols = [f"{c}_sd" for c in comps if f"{c}_sd" in df.columns]
            if sd_cols:
                df["player_pass_rush_yds_sd"] = np.sqrt(
                    df[sd_cols].pow(2).fillna(0).sum(axis=1)
                )

    if created:
        logging.info("Normalized projection columns for markets: %s", ", ".join(sorted(created)))

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

def advice_lines(df: pd.DataFrame, threshold: float) -> str:
    """Format human-readable advice lines for Slack/console."""
    if df is None or df.empty:
        return "No edges found."
    name_map = {
        "player_pass_yds": "passing yards",
        "player_rush_yds": "rushing yards",
        "player_reception_yds": "receiving yards",
        "player_receptions": "receptions",
        "player_pass_yds": "passing yards",
        "player_pass_tds": "pass TDs",
        "player_pass_longest_completion": "longest completion",
        "player_pass_rush_yds": "pass+rush yards",
        "player_pass_rush_reception_yds": "pass+rush+rec yards",
        "player_pass_rush_reception_tds": "pass+rush+rec TDs",
        "player_rush_tds": "rush TDs",
        "player_rush_attempts": "rush attempts",
        "player_reception_tds": "rec TDs",
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
        lines.append(
            f"{r['player']} {r['side']} {r['book_line']} {name_map.get(r['market_key'], r['market_key'])} — "
            f"{r['book_odds']} ({r['best_book']}) | EV {evp} | {r['stake_u']}u"
        )
    return "\n".join(lines)

def main() -> int:
    logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))

    # Prefer env var; else auto-pick latest raw_stats_YYYY_wkN.csv in data/
    pref = os.environ.get("PROJECTIONS_PATH", "").strip() or None
    proj_path, year, week = resolve_projection_path(pref)
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

    df_proj = load_projections(proj_path)
    cfg = load_config()

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

    os.makedirs("artifacts", exist_ok=True)
    if df_edges is not None and not df_edges.empty:
        df_edges.to_csv("artifacts/edges.csv", index=False)

    adv = advice_lines(df_edges, threshold)
    with open("artifacts/advice.txt", "w", encoding="utf-8") as f:
        f.write(adv + "\n")

    logging.info("\n=== ADVICE ===\n%s\n", adv)
    alert_edges(df_edges, threshold_ev=threshold)
    return 0

if __name__ == "__main__":
    sys.exit(main())
