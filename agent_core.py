import os, re, math, csv, json, time, datetime as dt, logging
from math import erf, sqrt
from typing import List, Dict, Tuple, Optional

import requests
import pandas as pd
import numpy as np

NFL_SPORT_KEY = "americanfootball_nfl"
USER_AGENT = "nfl-prop-agent/1.0"
CALL_LOG_PATH = os.environ.get("ODDS_API_CALL_LOG", "odds_api_calls.csv")

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)

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
    used = resp.headers.get("x-requests-used")
    remaining = resp.headers.get("x-requests-remaining")
    last_cost = resp.headers.get("x-requests-last")
    logging.info(f"[ODDS-API]{' '+tag if tag else ''} used={used} remaining={remaining} last_call_cost={last_cost}")
    row = {
        "ts_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
        "endpoint": resp.request.path_url,
        "tag": tag,
        "status": resp.status_code,
        "used": used, "remaining": remaining, "last_cost": last_cost
    }
    write_header = not os.path.exists(CALL_LOG_PATH)
    with open(CALL_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if write_header: w.writeheader()
        w.writerow(row)

def http_get_json(session: requests.Session, url: str, params: Dict, tag: str="") -> dict:
    # Retry 5xx only; fail fast on 4xx to avoid burning credits
    for attempt in range(3):
        resp = session.get(url, params=params, timeout=25)
        _log_usage(resp, tag=tag)
        if 500 <= resp.status_code < 600:
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}

# -------------------- Odds API wrappers + budgeting --------------------
def list_upcoming_events(api_key: str, sport_key: str = NFL_SPORT_KEY, days_from: int = 7) -> List[dict]:
    """Events call is free (does not charge credits)."""
    session = _requests_session()
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events"
    params = {"apiKey": api_key, "daysFrom": days_from}
    return http_get_json(session, url, params, tag="events_free")

def get_event_odds(api_key: str, event_id: str, regions: str, odds_format: str, markets_csv: str) -> dict:
    session = _requests_session()
    url = f"https://api.the-odds-api.com/v4/sports/{NFL_SPORT_KEY}/events/{event_id}/odds"
    params = {"apiKey": api_key, "regions": regions, "oddsFormat": odds_format, "markets": markets_csv}
    tag = f"event={event_id} markets={markets_csv}"
    return http_get_json(session, url, params, tag=tag)

def estimate_credits(num_events: int, markets: List[str], regions: str="us") -> int:
    n_markets = len(markets)
    n_regions = len(regions.split(",")) if isinstance(regions, str) else 1
    return num_events * n_markets * n_regions

# -------------------- Name & market helpers --------------------
def normalize_name(n: str) -> str:
    n = (n or "").lower().strip()
    n = re.sub(r"[.,'’]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n

DISCRETE_KEYS = {
    "player_receptions", "player_pass_tds", "player_rush_tds", "player_reception_tds",
    "player_pass_attempts", "player_pass_completions", "player_interceptions"
}

def is_discrete_market(market_key: str) -> bool:
    return market_key in DISCRETE_KEYS

def sanity_line_ok(market_key: str, line: float) -> bool:
    # Drop weird mismatches (saves bad edges + user time)
    if market_key.endswith("_yds"):
        return line is not None and line >= 5.5
    if market_key == "player_receptions":
        return line is not None and line >= 0.5
    if market_key.endswith("_tds"):
        return line in (0.5, 1.5)
    if market_key in {"player_pass_attempts", "player_pass_completions"}:
        return line is not None and line >= 5.5
    if market_key == "player_interceptions":
        return line in (0.5, 1.5)
    if market_key in {"player_longest_reception", "player_longest_rush"}:
        return line is not None and line >= 5.5
    return True

# -------------------- Variance blending --------------------
def make_variance_blend(row: pd.Series, market_key: str, sigma_cfg: dict, alpha: float) -> float:
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
def best_offer_for_player(event_json: dict, player_name: str, market_key: str, side: str, target_books: set):
    player_norm = normalize_name(player_name)
    best = None  # (book, line, price)
    for bm in event_json.get("bookmakers", []):
        bk = bm.get("key", "")
        if bk not in target_books:
            continue
        for mk in bm.get("markets", []):
            if mk.get("key") != market_key:
                continue
            for outc in mk.get("outcomes", []):
                desc = normalize_name(outc.get("description") or outc.get("name") or outc.get("participant") or "")
                if player_norm not in desc:
                    continue
                line = outc.get("point")
                price = outc.get("price")
                if line is None or price is None:
                    continue
                try:
                    line = float(line); price = int(price)
                except Exception:
                    continue
                if not sanity_line_ok(market_key, line):
                    continue
                cand = (bk, line, price)
                if best is None:
                    best = cand
                else:
                    if side == "OVER":
                        if cand[1] < best[1] or (cand[1] == best[1] and cand[2] > best[2]):
                            best = cand
                    else:
                        if cand[1] > best[1] or (cand[1] == best[1] and cand[2] > best[2]):
                            best = cand
    return best

# -------------------- Main scan --------------------
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
      regions, target_books, sigma_defaults, blend_alpha, markets{base,heavy},
      bankroll, unit_pct, stake_bands
    """
    regions = cfg.get("regions", "us")
    target_books = set(cfg.get("target_books", []))
    sigma_defaults = cfg.get("sigma_defaults", {})
    alpha = float(cfg.get("blend_alpha", 0.35))
    markets_list = cfg.get("markets", {}).get(profile, cfg.get("markets", {}).get("base", []))
    bankroll = float(cfg.get("bankroll", 1000.0))
    unit_pct = float(cfg.get("unit_pct", 0.01))
    stake_bands = cfg.get("stake_bands", [
        {"min_ev": 0.08, "stake_u": 1.0},
        {"min_ev": 0.04, "stake_u": 0.5},
        {"min_ev": 0.02, "stake_u": 0.3},
    ])

    # Estimate credits
    events = list_upcoming_events(api_key, days_from=days_from)
    num_events = len(events or [])
    est = estimate_credits(num_events, markets_list, regions=regions)
    logging.info(f"[BUDGET] events={num_events}; markets={len(markets_list)}; estimated_credits≈{est}")

    if est > max_calls and num_events > 0:
        logging.warning(f"[BUDGET] est {est} > max_calls {max_calls}. Trimming markets.")
        keep = max(1, max_calls // num_events)
        markets_list = markets_list[:keep]
        logging.info(f"[BUDGET] trimmed markets to {len(markets_list)}")

    event_map = {e["id"]: e for e in (events or [])}

    rows = []
    for event_id, ev in event_map.items():
        if not markets_list:
            break
        markets_csv = ",".join(markets_list)
        ev_json = get_event_odds(api_key, event_id, regions=regions, odds_format="american", markets=markets_csv)

        home = (ev.get("home_team") or "").upper()
        away = (ev.get("away_team") or "").upper()

        def team_matches(t: str) -> bool:
            if not t: return False
            T = t.upper()
            return (home.find(T) != -1) or (away.find(T) != -1) or (T.find(home) != -1) or (T.find(away) != -1)

        df_ev = projections[projections["team"].apply(team_matches)] if {"team"}.issubset(projections.columns) else projections

        for _, r in df_ev.iterrows():
            player = r.get("player") or r.get("name") or ""
            if not player: 
                continue
            for mkey in markets_list:
                if mkey not in r: 
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
                    ev_m120 = ev_per_unit(win_prob, -120)
                    ev_m110 = ev_per_unit(win_prob, -110)
                    ev_p100 = ev_per_unit(win_prob, 100)
                    playable = "YES" if ev_per_unit(win_prob, -115) > 0 else "NO"

                    unit_size = bankroll * unit_pct
                    stake_u = 0.0
                    for band in sorted(stake_bands, key=lambda x: x["min_ev"], reverse=True):
                        if ev_now >= band["min_ev"]:
                            stake_u = band["stake_u"]; break
                    stake_dollars = round(unit_size * stake_u, 2)

                    rows.append({
                        "player": player,
                        "team": r.get("team"),
                        "pos": (r.get("pos") or r.get("position")),
                        "market_key": mkey,
                        "side": side,
                        "proj_mean": round(mu, 3),
                        "model_sd": round(sd, 3),
                        "best_book": best_book,
                        "book_line": float(book_line),
                        "book_odds": int(book_odds),
                        "win_prob": round(win_prob, 4),
                        "ev_per_unit": round(ev_now, 4),
                        "playable@-115": playable,
                        "ev@-120": round(ev_m120, 4),
                        "ev@-110": round(ev_m110, 4),
                        "ev@100": round(ev_p100, 4),
                        "stake_u": stake_u,
                        "stake_$": stake_dollars,
                        "event_id": event_id,
                        "home_team": home,
                        "away_team": away
                    })

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values(["ev_per_unit", "win_prob"], ascending=[False, False], inplace=True, kind="mergesort")
        df.reset_index(drop=True, inplace=True)
    return df
