"""Data cleaning utilities for NFL prop projections.

This module normalizes projection CSVs into a consistent format used by
the NFL prop agent.  It performs the following steps:

* Renames common column headers to canonical names (player, team, pos).
* Filters the dataset to the offensive skill positions (QB, RB, WR, TE).
* Drops a long tail of DST/K/IDP/meta columns that are irrelevant for
  player prop betting.
* Maps projection stat aliases (e.g. ``pass_yds``) to their corresponding
  player prop market names (e.g. ``player_pass_yds``).
* Coerces numeric columns and converts obvious string NA tokens to NaN.
* Computes combo statistics such as pass+rush yards and pass+rush+rec yards
  along with their standard deviations.
* Removes rows where every market of interest is missing.

The goal is to produce a clean DataFrame where each market column
(``player_*``) contains numeric values or NaN, ready for scanning against
sportsbook lines.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Positions to keep – these are the offensive skill positions for which
# player prop markets are typically offered.  Defensive positions and
# special teams are removed early in the cleaning process.
_KEEP_POS = {"QB", "RB", "WR", "TE"}

# Columns to drop (exact names from your CSV)
#
# The projection files often include a wide array of statistics beyond
# player props (defensive scores, kicker stats, IDP metrics, and meta
# fields like birthdate).  The agent doesn’t use these fields, so we
# explicitly drop them to reduce memory and avoid accidentally carrying
# irrelevant data forward.
_DROP_COLS = [
    "two_pts", "two_pts_sd", "return_tds", "return_tds_sd",
    "fg_0019", "fg_0019_sd", "fg_2029", "fg_2029_sd", "fg_3039", "fg_3039_sd",
    "fg_4049", "fg_4049_sd", "fg_50", "fg_50_sd", "xp", "xp_sd",
    "dst_int", "dst_int_sd", "dst_sacks", "dst_sacks_sd", "dst_safety", "dst_safety_sd",
    "dst_td", "dst_td_sd", "dst_blk", "dst_blk_sd",
    "idp_solo", "idp_solo_sd", "idp_sack", "idp_sack_sd", "idp_int", "idp_int_sd",
    "idp_pd", "idp_pd_sd", "idp_td", "idp_td_sd",
    "birthdate", "draft_year", "injury_status", "injury_details",
]

# Markets you’re using
#
# The NFL prop agent primarily focuses on passing, rushing and receiving
# yardage/TD props.  In addition to the core six markets, we also map
# common projection column names to their corresponding player prop
# identifiers.  For example, ``rec`` (from most projection files) is
# mapped to ``player_receptions``, and ``pass_rush_yds`` (if present)
# is mapped to ``player_pass_rush_yds``.  These aliases allow the
# cleaning step to backfill the normalized ``player_*`` columns from
# whichever naming convention your projections provider uses.
_MARKET_MAP = {
    "pass_yds": "player_pass_yds",
    "pass_tds": "player_pass_tds",
    "rush_yds": "player_rush_yds",
    "rush_tds": "player_rush_tds",
    "rec_yds": "player_reception_yds",
    "rec_tds": "player_reception_tds",
    # New alias: receptions (rec) maps to player_receptions
    "rec": "player_receptions",
    # Alias for combined pass+rush yards.  Some projection files may
    # include a "pass_rush_yds" column; if so, map it to the player
    # prop identifier.  Even when this column is absent, we compute
    # the combination below.
    "pass_rush_yds": "player_pass_rush_yds",
}

# Any *_sd columns (if present) should be numeric too
_SD_SUFFIXES = ["_sd"]

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize base column names.

    Projections from different sources often label the same concept
    differently (e.g. "name" vs. "Player", "TEAM" vs. "team").  This
    function renames any alternate names to the canonical names
    (player, team, pos).  If the canonical "pos" column is missing
    entirely, a KeyError will be raised.
    """
    rename_map: dict[str, str] = {}
    for want, alts in {
        "player": ["Player", "PLAYER", "name", "Name"],
        "team": ["Team", "TEAM"],
        "pos": ["position", "Position", "POS", "Pos"],
    }.items():
        if want not in df.columns:
            for a in alts:
                if a in df.columns:
                    rename_map[a] = want
                    break
    if rename_map:
        df = df.rename(columns=rename_map)
    return df

def _add_market_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Backfill ``player_*`` market columns from common aliases if missing.

    This iterates through the alias→market map and creates the
    ``player_*`` columns where they don't already exist but an alias
    column does.  It also performs the same mapping for any standard
    deviation columns (e.g. ``pass_yds_sd`` → ``player_pass_yds_sd``).
    """
    for alias, market in _MARKET_MAP.items():
        # Copy over the core value column
        if market not in df.columns and alias in df.columns:
            df[market] = df[alias]
        # Copy over the corresponding _sd column
        alias_sd = f"{alias}_sd"
        market_sd = f"{market}_sd"
        if market_sd not in df.columns and alias_sd in df.columns:
            df[market_sd] = df[alias_sd]
    return df

def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Convert NA-like tokens to NaN and coerce market columns to numeric.

    Projections sometimes use strings like "NA", "null" or empty strings
    to denote missing values.  Replace these with actual NaN, then
    coerce all market columns and their standard deviations to numeric.
    Any non-convertible values will become NaN.
    """
    na_tokens = {"NA", "NaN", "None", "", "null", "Null", "NULL"}
    df = df.replace(list(na_tokens), np.nan)
    # Build a list of market columns currently present
    numeric_cols: list[str] = list(_MARKET_MAP.values()) + [
        f"{v}{sfx}"
        for v in _MARKET_MAP.values()
        for sfx in _SD_SUFFIXES
        if f"{v}{sfx}" in df.columns
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def clean_projections(df_in: pd.DataFrame) -> pd.DataFrame:
    """Clean a raw projection DataFrame.

    This high-level routine orchestrates the cleaning pipeline:

    * Normalize base columns and positions (player/team/pos).
    * Filter to the allowed positions.
    * Drop irrelevant columns.
    * Backfill alias-based market columns.
    * Coerce market columns to numeric.
    * Compute combined market columns (pass+rush and pass+rush+rec).
    * Retain a subset of useful columns, preserving others if desired.
    * Drop rows missing all markets.
    """
    df = df_in.copy()

    # Normalize base columns and positions
    df = _normalize_columns(df)
    if "pos" not in df.columns:
        raise KeyError("Missing required 'pos' (position) column after normalization.")

    # Filter to the positions you want
    df = df[df["pos"].astype(str).str.upper().isin(_KEEP_POS)].copy()

    # Drop unwanted columns if they exist
    drop_cols = [c for c in _DROP_COLS if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    # Backfill market columns (player_* names) from aliases
    df = _add_market_columns(df)

    # Coerce numeric for markets and *_sd variants
    df = _coerce_numeric(df)

    # ------------------------------------------------------------------
    # Compute combination stats
    # ------------------------------------------------------------------
    # player_pass_rush_yds: sum of pass and rush yards
    if "player_pass_rush_yds" not in df.columns:
        if "player_pass_yds" in df.columns or "player_rush_yds" in df.columns:
            df["player_pass_rush_yds"] = df[[c for c in ["player_pass_yds", "player_rush_yds"] if c in df.columns]].fillna(0).sum(axis=1)
    # Standard deviation for pass+rush yards: root-sum-of-squares
    if "player_pass_rush_yds_sd" not in df.columns:
        sd_components = [c for c in ["player_pass_yds_sd", "player_rush_yds_sd"] if c in df.columns]
        if sd_components:
            df["player_pass_rush_yds_sd"] = np.sqrt(df[sd_components].pow(2).fillna(0).sum(axis=1))

    # player_pass_rush_reception_yds: sum of pass, rush and reception yards
    if "player_pass_rush_reception_yds" not in df.columns:
        comps = [c for c in ["player_pass_yds", "player_rush_yds", "player_reception_yds"] if c in df.columns]
        if comps:
            df["player_pass_rush_reception_yds"] = df[comps].fillna(0).sum(axis=1)
    # Standard deviation for pass+rush+rec yards
    if "player_pass_rush_reception_yds_sd" not in df.columns:
        sd_comps = [c for c in ["player_pass_yds_sd", "player_rush_yds_sd", "player_reception_yds_sd"] if c in df.columns]
        if sd_comps:
            df["player_pass_rush_reception_yds_sd"] = np.sqrt(df[sd_comps].pow(2).fillna(0).sum(axis=1))

    # ------------------------------------------------------------------
    # Keep only useful columns (don’t accidentally drop player/team/pos/id/week)
    # ------------------------------------------------------------------
    # Base columns always retained if present
    keep_base = [c for c in ["player", "team", "pos", "id", "avg_type", "season_year", "week"] if c in df.columns]
    # Determine which market columns are present after mapping and combo
    keep_markets = [c for c in _MARKET_MAP.values() if c in df.columns]
    # Also include any combo markets we've created
    for combo_col in ["player_pass_rush_yds", "player_pass_rush_reception_yds"]:
        if combo_col in df.columns:
            keep_markets.append(combo_col)
    keep_markets = list(dict.fromkeys(keep_markets))  # deduplicate
    # Include SD columns for any present markets
    keep_markets_sd = [f"{c}_sd" for c in keep_markets if f"{c}_sd" in df.columns]

    # Preserve any other columns that your pipeline expects (safe approach):
    # If you prefer strict minimal columns, comment the next line and use only
    # keep_base + keep_markets + keep_markets_sd.
    preserved_others = [
        c
        for c in df.columns
        if c not in set(_DROP_COLS) and c not in set(keep_base + keep_markets + keep_markets_sd)
    ]

    final_cols = keep_base + keep_markets + keep_markets_sd + preserved_others
    df = df[final_cols]

    # Sanity: drop rows missing all markets
    if keep_markets:
        df = df.dropna(axis=0, subset=keep_markets, how="all")

    return df.reset_index(drop=True)
