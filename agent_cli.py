"""Command line interface for the NFL prop agent.

Loads projection data, runs the edge scanner, writes artifacts, and optionally
posts Slack alerts.  Configuration is pulled from ``agent_config.yaml`` via
``config.load_config`` to keep settings consistent with other entry points.
"""

from __future__ import annotations

import logging
import os
import re
import sys

import numpy as np
import pandas as pd

from agent_core import scan_edges
from alerts import alert_edges
from config import load_config


STAT_TO_MARKET = {
    "pass_yds": "player_pass_yds",
    "pass_yards": "player_pass_yds",
    "passing_yards": "player_pass_yds",
    "pass_tds": "player_pass_tds",
    "passing_tds": "player_pass_tds",
    "pass_int": "player_pass_interceptions",
    "pass_ints": "player_pass_interceptions",
    "passing_ints": "player_pass_interceptions",
    "pass_interceptions": "player_pass_interceptions",
    "passing_interceptions": "player_pass_interceptions",
    "interceptions_thrown": "player_pass_interceptions",
    "pass_attempts": "player_pass_attempts",
    "pass_att": "player_pass_attempts",
    "passing_attempts": "player_pass_attempts",
    "pass_comp": "player_pass_completions",
    "pass_completions": "player_pass_completions",
    "passing_completions": "player_pass_completions",
    "rush_yds": "player_rush_yds",
    "rush_yards": "player_rush_yds",
    "rushing_yards": "player_rush_yds",
    "rush_tds": "player_rush_tds",
    "rushing_tds": "player_rush_tds",
    "rush_att": "player_rush_attempts",
    "rush_attempts": "player_rush_attempts",
    "rushing_attempts": "player_rush_attempts",
    "rush_long": "player_rush_longest",
    "rush_longest": "player_rush_longest",
    "rushing_longest": "player_rush_longest",
    "rec": "player_receptions",
    "receptions": "player_receptions",
    "rec_yds": "player_reception_yds",
    "rec_yards": "player_reception_yds",
    "reception_yds": "player_reception_yds",
    "receiving_yards": "player_reception_yds",
    "rec_tds": "player_reception_tds",
    "reception_tds": "player_reception_tds",
    "receiving_tds": "player_reception_tds",
    "rec_long": "player_reception_longest",
    "rec_longest": "player_reception_longest",
    "receiving_longest": "player_reception_longest",
    "pass_long": "player_pass_longest_completion",
    "pass_longest": "player_pass_longest_completion",
    "pass_longest_completion": "player_pass_longest_completion",
    "pass_rush_rec_yds": "player_pass_rush_reception_yds",
    "pass_rush_rec_tds": "player_pass_rush_reception_tds",
}


def _normalize_alias(name: str) -> str:
    """Return a normalized key for projection/stat column names."""

    if not isinstance(name, str):
        return ""
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())
    key = re.sub(r"[^a-z0-9]+", "_", camel_split.lower())
    return key.strip("_")


def _ensure_market_columns(df: pd.DataFrame) -> None:
    """Backfill ``player_*`` market columns from common projection aliases."""

    alias_index = {}
    for col in df.columns:
        key = _normalize_alias(col)
        if key and key not in alias_index:
            alias_index[key] = col

    created = []
    created_sd = []
    for alias, market in STAT_TO_MARKET.items():
        src_col = alias_index.get(alias)
        if not src_col:
            continue
        if market not in df.columns:
            df[market] = df[src_col]
            created.append(market)
        alias_index[_normalize_alias(market)] = market

        sd_market = f"{market}_sd"
        sd_candidates = [
            f"{alias}_sd",
            f"{alias}_std",
            f"{alias}_stdev",
            f"{alias}_sigma",
        ]
        sd_src = None
        for cand in sd_candidates:
            col = alias_index.get(cand)
            if col:
                sd_src = col
                break
        if sd_src and sd_market not in df.columns:
            df[sd_market] = df[sd_src]
            created_sd.append(sd_market)
            alias_index[_normalize_alias(sd_market)] = sd_market

    # Combo helpers – these are additive stats that appear under multiple names.
    combo_created = []
    prr_base_cols = [
        "player_pass_yds",
        "player_rush_yds",
        "player_reception_yds",
    ]
    if "player_pass_rush_reception_yds" not in df.columns and all(
        col in df.columns for col in prr_base_cols
    ):
        df["player_pass_rush_reception_yds"] = df[prr_base_cols].fillna(0).sum(axis=1)
        combo_created.append("player_pass_rush_reception_yds")
        sd_cols = [f"{c}_sd" for c in prr_base_cols if f"{c}_sd" in df.columns]
        if sd_cols:
            df["player_pass_rush_reception_yds_sd"] = np.sqrt(
                df[sd_cols].pow(2).fillna(0).sum(axis=1)
            )
            combo_created.append("player_pass_rush_reception_yds_sd")

    prr_tds_base_cols = [
        "player_pass_tds",
        "player_rush_tds",
        "player_reception_tds",
    ]
    if "player_pass_rush_reception_tds" not in df.columns and all(
        col in df.columns for col in prr_tds_base_cols
    ):
        df["player_pass_rush_reception_tds"] = df[prr_tds_base_cols].fillna(0).sum(axis=1)
        combo_created.append("player_pass_rush_reception_tds")
        sd_cols = [f"{c}_sd" for c in prr_tds_base_cols if f"{c}_sd" in df.columns]
        if sd_cols:
            df["player_pass_rush_reception_tds_sd"] = np.sqrt(
                df[sd_cols].pow(2).fillna(0).sum(axis=1)
            )
            combo_created.append("player_pass_rush_reception_tds_sd")

    if created or created_sd or combo_created:
        logging.info(
            "Normalized projection columns for markets: %s",
            ", ".join(sorted(set(created + created_sd + combo_created))),
        )


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
    keep.sort_values(["ev_per_unit", "win_prob"], ascending=[False, False], inplace=True, kind="mergesort")
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
    days_from = int(os.environ.get("DAYS_FROM", cfg.get("days_from", 7)))
    max_calls = int(os.environ.get("MAX_CALLS", cfg.get("max_calls", 1000)))

    df_edges = scan_edges(
        df_proj,
        cfg,
        api_key=api_key,
        days_from=days_from,
        profile=profile,
        max_calls=max_calls,
    )

    os.makedirs("artifacts", exist_ok=True)
    if df_edges is not None and not df_edges.empty:
        df_edges.to_csv("artifacts/edges.csv", index=False)

    adv = advice_lines(df_edges, threshold)
    with open("artifacts/advice.txt", "w", encoding="utf-8") as f:
        f.write(adv + "\n")

    logging.info("\n=== ADVICE ===\n%s\n", adv)

    slack_webhook = cfg.get("slack_webhook_url")
    alert_edges(df_edges, threshold_ev=threshold, webhook=slack_webhook)
    return 0


if __name__ == "__main__":
    sys.exit(main())

