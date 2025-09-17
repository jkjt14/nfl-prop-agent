"""Command line interface for the NFL prop agent.

Loads projection data, runs the edge scanner, writes artifacts, and optionally
posts Slack alerts.  Configuration is pulled from ``agent_config.yaml`` via
``config.load_config`` to keep settings consistent with other entry points.
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
    "pass_rush_rec_yds": "player_pass_rush_reception_yds",
    "pass_rush_rec_tds": "player_pass_rush_reception_tds",
}


def _ensure_market_columns(df: pd.DataFrame) -> None:
    """Backfill ``player_*`` market columns from common projection aliases."""

    created = []
    for alias, market in STAT_TO_MARKET.items():
        if market not in df.columns and alias in df.columns:
            df[market] = df[alias]
            created.append(market)
        sd_alias = f"{alias}_sd"
        sd_market = f"{market}_sd"
        if sd_market not in df.columns and sd_alias in df.columns:
            df[sd_market] = df[sd_alias]

    # Combo helpers – these are additive stats that appear under multiple names.
    if "player_pass_rush_reception_yds" not in df.columns:
        comps = [c for c in ("pass_yds", "rush_yds", "rec_yds") if c in df.columns]
        if len(comps) == 3:
            df["player_pass_rush_reception_yds"] = df[comps].fillna(0).sum(axis=1)
            sd_cols = [f"{c}_sd" for c in comps if f"{c}_sd" in df.columns]
            if sd_cols:
                df["player_pass_rush_reception_yds_sd"] = np.sqrt(
                    df[sd_cols].pow(2).fillna(0).sum(axis=1)
                )

    if "player_pass_rush_reception_tds" not in df.columns:
        comps = [c for c in ("pass_tds", "rush_tds", "rec_tds") if c in df.columns]
        if len(comps) == 3:
            df["player_pass_rush_reception_tds"] = df[comps].fillna(0).sum(axis=1)
            sd_cols = [f"{c}_sd" for c in comps if f"{c}_sd" in df.columns]
            if sd_cols:
                df["player_pass_rush_reception_tds_sd"] = np.sqrt(
                    df[sd_cols].pow(2).fillna(0).sum(axis=1)
                )

    if created:
        logging.info("Normalized projection columns for markets: %s", ", ".join(sorted(created)))


def load_projections(path: str) -> pd.DataFrame:
    """Load projection CSV into a DataFrame, normalizing column names."""
    if not os.path.exists(path):
        alt = "data/raw_stats_current.csv"
        if os.path.exists(alt):
            path = alt
    if not os.path.exists(path):
        raise FileNotFoundError(f"Projections CSV not found at {path}.")
    df = pd.read_csv(path)
    for c in ("player", "team", "pos"):
        if c not in df.columns:
            for altc in [c.title(), c.upper(), "Position" if c == "pos" else c]:
                if altc in df.columns:
                    df.rename(columns={altc: c}, inplace=True)
                    break
    _ensure_market_columns(df)
    logging.info(
        "Loaded projections: %s rows, %s cols from %s", len(df), len(df.columns), path
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
        "player_pass_tds": "pass TDs",
        "player_pass_longest_completion": "longest completion",
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
    """Run the scan and emit artifacts/alerts."""
    logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))

    proj_path = os.environ.get("PROJECTIONS_PATH", "data/projections.csv")
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        logging.error("Missing ODDS_API_KEY.")
        return 2

    threshold = float(os.environ.get("EDGE_THRESHOLD", "0.06"))
    profile = os.environ.get("MARKETS_PROFILE", "base")
    df_proj = load_projections(proj_path)

    cfg = load_config()
    df_edges = scan_edges(
        df_proj,
        cfg,
        api_key=api_key,
        days_from=7,
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

