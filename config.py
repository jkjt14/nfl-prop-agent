"""Configuration loader for the NFL prop agent.

This module centralizes loading of configuration values from
``agent_config.yaml`` so that both the CLI and the Streamlit app read the
same settings.  The structure returned by :func:`load_config` matches what
``scan_edges`` expects.
"""

from __future__ import annotations

import os
from difflib import get_close_matches
from typing import Any, Dict, Iterable, List

import yaml

KNOWN_BOOKMAKER_KEYS = {
    "fanduel",
    "draftkings",
    "betmgm",
    "williamhill_us",
    "espnbet",
    "fanatics",
    "ballybet",
    "betrivers",
    "sugarhouse",
    "unibet_us",
    "betonlineag",
    "bovada",
    "betanysports",
    "mybookieag",
    "betparx",
    "pointsbetus",
    "superbook",
    "wynnbet",
    "lowvig",
    "betus",
    "betfred",
    "pinnacle",
    "matchbook",
    "caesars",
    "circasports",
    "hardrock",
    "playup",
    "twinspires",
    "barstool",
    "ladbrokes_uk",
    "unibet_eu",
    "888sport",
    "williamhill",
    "coral",
}


def load_config(path: str = "agent_config.yaml") -> Dict[str, Any]:
    """Load configuration from ``path``.

    Parameters
    ----------
    path:
        Location of the YAML config file.

    Returns
    -------
    dict
        Mapping suitable for ``scan_edges``.
    """

    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found at {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Normalize market profiles.  Older configs listed markets as a flat list,
    # while newer ones already namespace them by profile (e.g. {base: [...],
    # heavy: [...]}).  ``scan_edges`` expects a mapping of profile → list, so
    # detect the shape here instead of blindly wrapping the value in another
    # ``{"base": ...}`` layer.
    markets_cfg = raw.get("markets", [])
    if isinstance(markets_cfg, dict):
        markets_by_profile = {}
        for profile, markets in markets_cfg.items():
            if isinstance(markets, str):
                values = [markets]
            elif isinstance(markets, (list, tuple, set)):
                values = list(markets)
            elif markets is None:
                values = []
            else:
                values = [markets]
            markets_by_profile[str(profile)] = values
    elif isinstance(markets_cfg, (list, tuple)):
        markets_by_profile = {"base": list(markets_cfg)}
    else:
        markets_by_profile = {"base": []}

    # Adapt keys from YAML to the structure used throughout the codebase.
    return {
        "regions": raw.get("regions", "us"),
        "target_books": raw.get("target_books", []),
        "markets": markets_by_profile,
        # YAML may use either outcome_sigma or sigma_defaults naming.
        "sigma_defaults": raw.get("outcome_sigma", raw.get("sigma_defaults", {})),
        "blend_alpha": raw.get("blend_alpha", 0.35),
        "bankroll": raw.get("bankroll", 1000.0),
        "unit_pct": raw.get("unit_pct", 0.01),
        # Staking bands appear as "ev_bands" or "stake_bands".
        "stake_bands": raw.get("ev_bands", raw.get("stake_bands", [])),
        "odds_levels": raw.get("odds_levels", [-120, -110, 100]),
        "max_juice": raw.get("max_juice"),
        "top_n": raw.get("top_n", 0),
        "odds_format": raw.get("odds_format", "american"),
    }


def validate_target_books(target_books: Iterable[str]) -> Dict[str, List[str]]:
    """Return unknown target book keys and suggestion list.

    Parameters
    ----------
    target_books:
        Iterable of bookmaker keys configured for a scan.

    Returns
    -------
    dict
        Dictionary with ``unknown`` (sorted list of invalid keys) and a
        ``suggestions`` mapping of each invalid key to close matches from the
        Odds API bookmaker catalog.
    """

    unique_books = {str(b).strip() for b in target_books if str(b).strip()}
    unknown = sorted(b for b in unique_books if b not in KNOWN_BOOKMAKER_KEYS)
    suggestions: Dict[str, List[str]] = {}
    if unknown:
        catalog = sorted(KNOWN_BOOKMAKER_KEYS)
        for book in unknown:
            matches = get_close_matches(book, catalog, n=3, cutoff=0.6)
            if matches:
                suggestions[book] = matches
    return {"unknown": unknown, "suggestions": suggestions}

