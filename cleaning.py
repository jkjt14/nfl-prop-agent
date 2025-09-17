# cleaning.py
from __future__ import annotations

import numpy as np
import pandas as pd

# Positions to keep
_KEEP_POS = {"QB", "RB", "WR", "TE"}

# Columns to drop (exact names from your CSV)
_DROP_COLS = [
    "two_pts","two_pts_sd","return_tds","return_tds_sd",
    "fg_0019","fg_0019_sd","fg_2029","fg_2029_sd","fg_3039","fg_3039_sd",
    "fg_4049","fg_4049_sd","fg_50","fg_50_sd","xp","xp_sd",
    "dst_int","dst_int_sd","dst_sacks","dst_sacks_sd","dst_safety","dst_safety_sd",
    "dst_td","dst_td_sd","dst_blk","dst_blk_sd",
    "idp_solo","idp_solo_sd","idp_sack","idp_sack_sd","idp_int","idp_int_sd",
    "idp_pd","idp_pd_sd","idp_td","idp_td_sd",
    "birthdate","draft_year","injury_status","injury_details",
]

# Markets you’re using
_MARKET_MAP = {
    "pass_yds": "player_pass_yds",
    "pass_tds": "player_pass_tds",
    "rush_yds": "player_rush_yds",
    "rush_tds": "player_rush_tds",
    "rec_yds": "player_reception_yds",
    "rec_tds": "player_reception_tds",
}

# Any *_sd columns (if present) should be numeric too
_SD_SUFFIXES = ["_sd"]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Ensure canonical column names exist: player, team, pos
    rename_map = {}
    for want, alts in {
        "player": ["Player", "PLAYER", "name", "Name"],
        "team":   ["Team", "TEAM"],
        "pos":    ["position", "Position", "POS", "Pos"],
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
    """Backfill player_* market columns from common aliases if missing."""
    for alias, market in _MARKET_MAP.items():
        if market not in df.columns and alias in df.columns:
            df[market] = df[alias]
        # also map *_sd if present
        alias_sd = f"{alias}_sd"
        market_sd = f"{market}_sd"
        if market_sd not in df.columns and alias_sd in df.columns:
            df[market_sd] = df[alias_sd]
    return df


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    # Convert obvious NA tokens to proper NaN, then coerce numeric in market cols
    na_tokens = {"NA", "NaN", "None", "", "null", "Null", "NULL"}
    df = df.replace(list(na_tokens), np.nan)
    numeric_cols = list(_MARKET_MAP.values()) + [
        f"{v}{sfx}" for v in _MARKET_MAP.values() for sfx in _SD_SUFFIXES
        if f"{v}{sfx}" in df.columns
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def clean_projections(df_in: pd.DataFrame) -> pd.DataFrame:
    """
    - Keep only QB/RB/WR/TE
    - Drop the long tail of DST/K/IDP/meta fields you listed
    - Ensure canonical player/team/pos
    - Ensure the six market columns exist and are numeric
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

    # Keep only useful columns (don’t accidentally drop player/team/pos/id/week)
    keep_base = [c for c in ["player", "team", "pos", "id", "avg_type", "season_year", "week"] if c in df.columns]
    keep_markets = [c for c in _MARKET_MAP.values() if c in df.columns]
    keep_markets_sd = [f"{c}_sd" for c in keep_markets if f"{c}_sd" in df.columns]

    # Preserve any other columns that your pipeline expects (safe approach):
    # If you prefer strict minimal columns, comment the next line and use only keep_base + keep_markets + keep_markets_sd.
    preserved_others = [c for c in df.columns if c not in set(_DROP_COLS) and c not in set(keep_base + keep_markets + keep_markets_sd)]

    final_cols = keep_base + keep_markets + keep_markets_sd + preserved_others
    df = df[final_cols]

    # sanity: drop rows missing all six markets
    if keep_markets:
        df = df.dropna(axis=0, subset=keep_markets, how="all")

    return df.reset_index(drop=True)
