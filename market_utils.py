"""Utilities for working with Odds API market keys.

The Odds API exposes player prop markets using compact identifiers such
as ``player_pass_yds`` or ``player_reception_tds`` while projection
providers often publish columns with longer names like
``player_pass_yards`` or ``player_receiving_touchdowns``.  To keep the
agent flexible across the different naming schemes we treat these
variants as synonyms.  This module exposes helpers to map between the
representations and to locate the appropriate projection column for a
given market.
"""

from __future__ import annotations

from typing import Iterator, Optional, Sequence, Set

# Alias → canonical Odds API key.  The canonical form (value) is the
# identifier accepted by the Odds API.  We list the longer projection
# column names as aliases so callers can canonicalize in either
# direction.
MARKET_KEY_ALIASES = {
    "player_pass_yards": "player_pass_yds",
    "player_pass_touchdowns": "player_pass_tds",
    "player_rush_yards": "player_rush_yds",
    "player_rush_touchdowns": "player_rush_tds",
    "player_receiving_yards": "player_reception_yds",
    "player_receiving_touchdowns": "player_reception_tds",
    # Common combined stat abbreviations used by projection providers.
    "player_pass_rush_yards": "player_pass_rush_yds",
    "player_pass_rush_reception_yards": "player_pass_rush_reception_yds",
    "player_pass_rush_reception_touchdowns": "player_pass_rush_reception_tds",
}

_CANONICAL_LOOKUP = {alias: canonical for alias, canonical in MARKET_KEY_ALIASES.items()}
for canonical in list(MARKET_KEY_ALIASES.values()):
    _CANONICAL_LOOKUP.setdefault(canonical, canonical)

_MARKET_SYNONYMS: dict[str, Set[str]] = {}


def _union(a: str, b: str) -> None:
    """Union the synonym sets for ``a`` and ``b``."""

    set_a = _MARKET_SYNONYMS.get(a, {a})
    set_b = _MARKET_SYNONYMS.get(b, {b})
    merged = set_a | set_b
    for key in merged:
        _MARKET_SYNONYMS[key] = merged


for alias, canonical in MARKET_KEY_ALIASES.items():
    _union(alias, canonical)


def canonical_market_key(key: str) -> str:
    """Return the Odds API canonical market key for ``key``."""

    return _CANONICAL_LOOKUP.get(key, key)


def market_synonyms(key: str) -> Set[str]:
    """Return the set of known synonym keys for ``key``."""

    canonical = canonical_market_key(key)
    synonyms = set()
    synonyms.update(_MARKET_SYNONYMS.get(key, {key}))
    synonyms.update(_MARKET_SYNONYMS.get(canonical, {canonical}))
    synonyms.add(key)
    synonyms.add(canonical)
    return {k for k in synonyms if k}


def iter_market_synonyms(key: str) -> Iterator[str]:
    """Yield synonym candidates for ``key`` in a deterministic order."""

    seen: Set[str] = set()
    for cand in (key, canonical_market_key(key)):
        if cand and cand not in seen:
            seen.add(cand)
            yield cand
    for cand in sorted(market_synonyms(key)):
        if cand and cand not in seen:
            seen.add(cand)
            yield cand


def resolve_market_column(columns: Sequence[str], market_key: str) -> Optional[str]:
    """Return the column name from ``columns`` that matches ``market_key``.

    Parameters
    ----------
    columns:
        Iterable of available column names (e.g. ``DataFrame.columns``).
    market_key:
        Requested market identifier (alias or canonical form).

    Returns
    -------
    str or None
        The matching column name, or ``None`` if no synonym is present.
    """

    available = set(columns)
    for cand in iter_market_synonyms(market_key):
        if cand in available:
            return cand
    return None

