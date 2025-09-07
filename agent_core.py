from __future__ import annotations

import os, re, math, csv, json, time, datetime as dt, logging
from math import erf, sqrt
from typing import List, Dict, Tuple, Optional

import requests
import pandas as pd
import numpy as np

# -------------------- constants / global config --------------------
NFL_SPORT_KEY = "americanfootball_nfl"
USER_AGENT = "nfl-prop-agent/1.1"
CALL_LOG_PATH = os.environ.get("ODDS_API_CALL_LOG", "odds_api_calls.csv")

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)

# Human-readable market names (for advice & alerts)
MARKET_READABLE = {
    "player_pass_yds": "passing yards",
    "player_rush_yds": "rushing yards",
    "player_reception_yds": "receiving yards",
    "player_receptions": "receptions",
    "player_pass_tds": "pass TDs",
    "player_rush_tds": "rush TDs",
    "player_reception_tds": "rec TDs",
    "player_longest_reception": "longest reception",
    "player_longest_rush": "longest rush",
    "player_pass_attempts": "pass attempts",
    "player_pass_completions": "pass completions",
    "player_interceptions": "interceptions",
}

# Markets that should use continuity correction
DISCRETE_KEYS = {
    "player_receptions", "player_pass_tds", "player_rush_tds", "player_reception_tds",
    "player_pass_attempts", "player_pass_completions", "player_interceptions"
}

# -------------------- Odds & probability helpers --------------------
def american_to_implied_p(odds: int) -> float:
    return (-odds)/((-odds)+100) if odds < 0 else 100/(odds+100)

def normal_cdf(x: float, mu: float, sd: float) -> float:
    if sd <= 0:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / sd
    return 0.5 * (1 + erf(z / sqrt(2)))

def prob_over(line: float, mu: float, sd: float, is_discrete: bool=False) -> float:
    # continuity correction for counts
    return 1 - normal_cdf(line + (0.5 if is_discrete else 0.0), mu, sd)

def ev_per_unit(p: float, american_odds: int) -> float:
    b = (100/abs(american_odds)) if american_odds < 0 else (american_odds/100)
    return p*b - (1-p)*1

def kelly_fraction(p: float, american_odds: int) -> float:
    b = (100/abs(american_odds)) if american_odds < 0 else (american_odds/100)
    if b <= 0: return 0.0
    q = 1 - p
    return max(0.0, (b*p - q) / b)

# -------------------- HTTP + usage logging --------------------
def _requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s

def _log_usage(resp: requests.Response, tag: str=""):
    try:
        used = resp.headers.get("x-requests-used")
        remaining = resp.headers.get("x-requests-remaining")
        last_cost = resp.headers.get("x-requests-last")
        path = getattr(resp.request, "path_url", "") if resp.request is not None else ""
        logging.info(f"[ODDS-API]{' '+tag if tag else ''} used={used} remaining={remaining} last_call_cost={last_cost} path={path}")
        row = {
            "ts_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
            "endpoint": path,
            "tag": tag,
            "status": resp.status_code,
            "used": used, "remaining": remaining, "last_cost": last_cost
        }
        write_header = not os.path.exists(CALL_LOG_PATH)
        with open(CALL_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=row.keys())
            if write_header: w.writeheader()
            w.writerow(row)
    except Exception as e:
        logging.debug(f"_log_usage skipped: {e}")

def http_get_json(session: requests.Session, url: str, params: Dict, tag: str="") -> dict:
    # Retry 5xx only; fail fast on 4xx to avoid burning credits
    last = None
    for attempt in range(3):
        resp = session.get(url, params=params, timeout=25)
        _log_usage(resp, tag=tag)
        if 500 <= resp.status_code < 600:
            last = resp
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    if last is not None:
        last.raise_for_status()
    raise RuntimeError("http_get_json: unreachable")

# -------------------- Odds API wrappers + budgeting --------------------
def list_upcoming_events(api_key: str, sport_key: str = NFL_SPORT_KEY, days_from: int = 7) -> List[dict]:
    """Events call is free (does not charge credits)."""
    session = _requests_session()
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events"
    params = {"apiKey": api_key, "daysFrom": int(days_from)}
    return http_get_json(session, url, params, tag="events_free")

def get_event_odds(api_key: str, event_id: str, regions: str, odds_format: str, markets: str) -> dict:
    session = _requests_session()
    url = f"https://api.the-odds-api.com/v4/sports/{NFL_SPORT_KEY}/events/{event_id}/odds"
    params = {"apiKey": api_key, "regions": regions, "oddsFormat": odds_format, "markets": markets}
    tag = f"event={event_id} markets={markets}"
    return http_get_json(session, url, params, tag=tag)

def estimate_credits(num_events: int, markets: List[str], regions: str="us") -> int:
    n_markets = len(markets)
    n_regions = len(regions.split(",")) if isinstance(regions, str) else 1
    return num_events * max(1, n_markets) * max(1, n_regions)

# -------------------- Name & market helpers --------------------
def normalize_name(n: str) -> str:
    n = (n or "").lower().strip()
    n = re.sub(r"[.,'’]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n

def is_discrete_market(market_key: str) -> bool:
    return market_key in DISCRETE_KEYS

def sanity_line_ok(market_key: str, line: Optional[float]) -> bool:
    if line is None:
        return False
    try:
        x = float(line)
    except Exception:
        return False
    # Drop weird mismatches (saves bad edges + user time)
    if market_key.endswith("_yds"):
        return x >= 5.5
    if market_key == "player_receptions":
        return x >= 0.5
    if market_key.endswith("_tds"):
        return x in (0.5, 1.5)
    if market_key in {"player_pass_attempts", "player_pass_completions"}:
        return x >= 5.5
    if market_key == "player_interceptions":
        return x in (0.5, 1.5)
    if market_key in {"player_longest_reception", "player_longest_rush"}:
        return x >= 5.5
    return True

# -------------------- Variance blending --------------------
def make_variance_blend(row: pd.Series, market_key: str, sigma_cfg: dict, alpha: float) -> float:
    """
    Blend per-player sd (if present in projections) with position default sd.
    sigma_cfg: { "QB": {"player_pass_yds": 45.0, ...}, "WR": {...}, ... }
    """
    pos = str(row.get("pos") or row.get("position") or "").upper()
    src_sd = None
    for cand in [f"{market_key}_sd", market_key.replace("player_", "") + "_sd"]:
        if cand in row and pd.notna(row[cand]):
            try:
                src_sd = float(row[cand]); break
            except Exception:
                pass
    base_sigma = float(sigma_cfg.get(pos, {}).get(market_key, 25.0))
    if src_sd is None:
        return base_sigma
    return math.sqrt(alpha * (src_sd ** 2) + (1 - alpha) * (base_sigma ** 2))

# -------------------- Best offer per player --------------------
def _names_match(player_needle: str, cand: str) -> bool:
    """Loose but safe player-name match.
    Tries full-name containment, token subset, and last-name fallback."""
    if not player_needle or not cand:
        return False
    needle = normalize_name(player_needle)
    hay = normalize_name(cand)
    if not needle or not hay:
        return False
    if needle in hay or hay in needle:
        return True
    # token subset check (e.g., "joe burrow" vs "burrow joe over")
    n_tokens = set(needle.split())
    h_tokens = set(hay.split())
    if n_tokens.issubset(h_tokens) or h_tokens.issubset(n_tokens):
        return True
    # last-name fallback (common when books show only the surname)
    last = needle.split()[-1]
    if len(last) >= 4 and last in h_tokens:
        return True
    return False

def _normalize_side(s: str) -> str:
    s = normalize_name(s or "")
    if s.startswith("over"):
        return "OVER"
    if s.startswith("under"):
        return "UNDER"
    return ""  # unknown / not provided

def best_offer_for_player(
    event_json: dict,
    player_name: str,
    market_key: str,
    side: str,
    target_books: set
):
    """
    Returns (book, line, price) for the best available outcome for a given player+market+side.
    Selection logic:
      - OVER  => prefer the LOWEST line, then better (higher) price
      - UNDER => prefer the HIGHEST line, then better (higher) price
    Only applies book filtering if target_books is non-empty.
    """
    want_side = (_normalize_side(side) or "OVER")  # default to OVER if malformed
    player_norm = normalize_name(player_name)
    best: Optional[Tuple[str, float, int]] = None

    for bm in ((event_json or {}).get("bookmakers", []) or []):
        bk = bm.get("key", "") or ""
        # filter only if explicit book list is provided
        if target_books and bk not in target_books:
            continue

        for mk in (bm.get("markets", []) or []):
            if mk.get("key") != market_key:
                continue

            for outc in (mk.get("outcomes", []) or []):
                # Try multiple fields books may use
                # Player label may be in 'name' or 'participant'; 'description' often holds "Over/Under"
                player_label = (
                    outc.get("participant")
                    or outc.get("name")
                    or outc.get("description")
                    or ""
                )
                desc_side = (
                    outc.get("description")
                    or outc.get("label")
                    or outc.get("side")
                    or ""
                )
                have_side = _normalize_side(desc_side)

                # Side must match if the book provides it explicitly
                if have_side and have_side != want_side:
                    continue

                # Ensure the outcome is for the right player
                if not _names_match(player_norm, player_label):
                    # Some feeds invert fields: player in 'name', side in 'description' (already handled),
                    # but also try 'outc.get("name")' vs 'outc.get("description")' cross-wise:
                    alt_player_label = outc.get("name") or outc.get("participant") or ""
                    if not _names_match(player_norm, alt_player_label):
                        continue

                # Line / odds fields vary by book
                raw_line = outc.get("point", outc.get("handicap", outc.get("line")))
                raw_price = outc.get("price", outc.get("odds", outc.get("american")))

                if raw_line is None or raw_price is None:
                    continue

                # Cast robustly
                try:
                    line = float(raw_line)
                except Exception:
                    continue
                try:
                    price = int(str(raw_price))
                except Exception:
                    # handle "+120" or "120" style strings
                    try:
                        price = int(str(raw_price).replace("+", "").strip())
                        # if it had '+' removed, restore sign (+ becomes positive, which is fine)
                    except Exception:
                        continue

                if not sanity_line_ok(market_key, line):
                    continue

                cand = (bk, line, price)

                if best is None:
                    best = cand
                else:
                    # OVER: prefer lower line; UNDER: prefer higher line; tie-break on better (higher) price
                    if want_side == "OVER":
                        if (cand[1] < best[1]) or (cand[1] == best[1] and cand[2] > best[2]):
                            best = cand
                    else:  # UNDER
                        if (cand[1] > best[1]) or (cand[1] == best[1] and cand[2] > best[2]):
                            best = cand

    return best


# -------------------- Main scan --------------------
def _resolve_markets(cfg: dict, profile: str) -> List[str]:
    """
    cfg["markets"] can be either a dict of profiles or a flat list.
    """
    markets_cfg = cfg.get("markets", [])
    if isinstance(markets_cfg, dict):
        return list(markets_cfg.get(profile) or markets_cfg.get("base") or [])
    if isinstance(markets_cfg, list):
        return list(markets_cfg)
    return []

def scan_edges(
    projections: pd.DataFrame,
    cfg: dict,
    *,
    api_key: str,
    days_from: int = 7,
    profile: str = "base",
    max_calls: int = 1000
) -> pd.DataFrame:
    """
    projections must include ['player','team','pos'] and market means like 'player_pass_yds', etc.
    cfg keys:
      regions: "us" or "us,eu", ...
      target_books: ["fanduel","draftkings",...]
      sigma_defaults: { "QB": {"player_pass_yds": 45.0, ...}, ... }
      blend_alpha: 0..1
      markets: list OR dict of profiles { "base":[...], "heavy":[...] }
      bankroll: float
      unit_pct: float (e.g. 0.01)
      stake_bands: [{min_ev:0.08, stake_u:1.0}, ...]
    """
    regions = cfg.get("regions", "us")
    target_books = set(cfg.get("target_books", []))  # empty set means allow all books
    sigma_defaults = cfg.get("sigma_defaults", {})
    alpha = float(cfg.get("blend_alpha", 0.35))
    markets_list = _resolve_markets(cfg, profile)
    bankroll = float(cfg.get("bankroll", 1000.0))
    unit_pct = float(cfg.get("unit_pct", 0.01))
    stake_bands = cfg.get("stake_bands", [
        {"min_ev": 0.08, "stake_u": 1.0},
        {"min_ev": 0.04, "stake_u": 0.5},
        {"min_ev": 0.02, "stake_u": 0.3},
    ])

    # Estimate credits and trim if needed
    events = list_upcoming_events(api_key, days_from=days_from) or []
    num_events = len(events)
    est = estimate_credits(num_events, markets_list, regions=regions)
    logging.info(f"[BUDGET] events={num_events}; markets={len(markets_list)}; estimated_credits≈{est}")

    if est > max_calls and num_events > 0:
        logging.warning(f"[BUDGET] est {est} > max_calls {max_calls}. Trimming markets.")
        keep = max(1, max_calls // num_events)
        markets_list = markets_list[:keep]
        logging.info(f"[BUDGET] trimmed markets to {len(markets_list)}")

    if not markets_list:
        logging.warning("No markets selected. Returning empty DataFrame.")
        return pd.DataFrame()

    event_map = {e.get("id"): e for e in events if e.get("id")}

    rows: List[dict] = []
    for event_id, ev in event_map.items():
        markets_csv = ",".join(markets_list)
        try:
            ev_json = get_event_odds(api_key, event_id, regions=regions, odds_format="american", markets=markets_csv)
        except Exception as e:
            logging.warning(f"get_event_odds failed for {event_id}: {e}")
            continue

        home = (ev.get("home_team") or "").upper()
        away = (ev.get("away_team") or "").upper()

        def team_matches(t: str) -> bool:
            if not t: return False
            T = str(t).upper()
            return (home.find(T) != -1) or (away.find(T) != -1) or (T.find(home) != -1) or (T.find(away) != -1)

        if "team" in projections.columns:
            df_ev = projections[projections["team"].apply(team_matches)].copy()
        else:
            df_ev = projections.copy()

        for _, r in df_ev.iterrows():
            player = r.get("player") or r.get("name") or ""
            if not player:
                continue
            for mkey in markets_list:
                if mkey not in r or pd.isna(r[mkey]):
                    continue
                try:
                    mu = float(r[mkey])
                except Exception:
                    continue

                sd = make_variance_blend(r, mkey, sigma_defaults, alpha)
                for side in ("OVER", "UNDER"):
                    offer = best_offer_for_player(ev_json, player, mkey, side, target_books)
                    if not offer:
                        continue
                    best_book, book_line, book_odds = offer

                    p_over = prob_over(book_line, mu, sd, is_discrete=is_discrete_market(mkey))
                    win_prob = p_over if side == "OVER" else (1 - p_over)

                    ev_now = ev_per_unit(win_prob, book_odds)
                    playable = "YES" if ev_per_unit(win_prob, -115) > 0 else "NO"

                    unit_size = bankroll * unit_pct
                    stake_u = 0.0
                    for band in sorted(stake_bands, key=lambda x: x["min_ev"], reverse=True):
                        if ev_now >= band["min_ev"]:
                            stake_u = float(band["stake_u"]); break
                    stake_dollars = round(unit_size * stake_u, 2)

                    market_readable = MARKET_READABLE.get(mkey, mkey.replace("player_", "").replace("_", " "))

                    # Primary row (rich detail)
                    rows.append({
                        "player": player,
                        "team": r.get("team"),
                        "pos": (r.get("pos") or r.get("position")),
                        "market_key": mkey,
                        "market_readable": market_readable,
                        "side": side,
                        "proj_mean": round(mu, 3),
                        "model_sd": round(sd, 3),
                        "best_book": best_book,
                        "book_line": float(book_line),
                        "book_odds": int(book_odds),
                        "win_prob": round(win_prob, 4),
                        "ev_per_unit": round(ev_now, 4),
                        "playable@-115": playable,
                        "stake_u": stake_u,
                        "stake_$": stake_dollars,
                        "event_id": event_id,
                        "home_team": home,
                        "away_team": away,
                    })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # Add alert-friendly alias columns so alerts.alert_edges() can consume directly
    df["line"] = df["book_line"]
    df["book"] = df["best_book"]
    df["price"] = df["book_odds"]
    df["ev"] = df["ev_per_unit"]

    # Advice string (nice to print / log)
    def _fmt_row(s: pd.Series) -> str:
        try:
            odds_str = f"{int(s['price']):+d}"
        except Exception:
            odds_str = str(s.get("price"))
        return (
            f"{s.get('player','')} {s.get('side','')} {s.get('line',''):.1f} "
            f"{s.get('market_readable','')} — {s.get('book','')} {odds_str} | "
            f"EV {float(s.get('ev',0))*100:.1f}% • stake {float(s.get('stake_u',0)):.1f}u"
        )

    df["advice"] = df.apply(_fmt_row, axis=1)

    # Order by EV desc, then win_prob desc
    df.sort_values(["ev_per_unit", "win_prob"], ascending=[False, False], inplace=True, kind="mergesort")
    df.reset_index(drop=True, inplace=True)
    return df
