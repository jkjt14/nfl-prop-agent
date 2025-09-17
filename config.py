"""Configuration loader for the NFL prop agent.

This module centralizes loading of configuration values from
``agent_config.yaml`` so that both the CLI and the Streamlit app read the
same settings.  The structure returned by :func:`load_config` matches what
``scan_edges`` expects.
"""

from __future__ import annotations

import os
from typing import Any, Dict

import yaml


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

    # Adapt keys from YAML to the structure used throughout the codebase.
    return {
        "regions": raw.get("regions", "us"),
        "target_books": raw.get("target_books", []),
        # Treat the top-level "markets" list as the default profile.
        "markets": {"base": raw.get("markets", [])},
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

