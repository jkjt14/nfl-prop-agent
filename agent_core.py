# agent_core.py
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
    """Convert American odds to an implied win probability."""
    return (-odds)/((-odds)+100) if odds < 0 else 100/(odds+100)

def normal_cdf(x: float, mu: float, sd: float) -> float:
    """Return the CDF of ``N(mu, sd^2)`` at ``x``.

    A zero or negative standard deviation yields a step function at ``mu``
    instead of raising.
    """
    if sd <= 0:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / sd
    return 0.5 * (1 + erf(z / sqrt(2)))

def prob_over(line: float, mu: float, sd: float, is_discrete: bool = False) -> float:
    """Probability the outcome exceeds ``line`` given mean ``mu``/sd ``sd``."""
    return 1 - normal_cdf(line + (0.5 if is_discrete else 0.0), mu, sd)

def ev_per_unit(p: float, american_odds: int) -> float:
    """Expected profit per 1 unit wagered."""
    b = (100/abs(american_odds)) if american_odds < 0 else (american_odds/100)
    return p * b - (1 - p) * 1

def kelly_fraction(p: float, american_odds: int) -> float:
    """Fraction of bankroll to wager according to the Kelly criterion."""
    b = (100/abs(american_odds)) if american_odds < 0 else (american_odds/100)
    if b <= 0:
        return 0.0
    q = 1 - p
    return max(0.0, (b * p - q) / b)

# -------------------- HTTP + usage logging --------------------
def _requests_session() -> requests.Session:
    """Return a ``requests.Session`` with our user agent set."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s

def _log_usage(resp: requests.Response, tag: str = "") -> None:
    """Persist Odds API usage headers for later inspection."""
    used = resp.headers.get("x-requests-used")
    remaining = resp.headers.get("x-requests-remaining")
    last_cost = resp.headers.get("x-requests-last")
    logging.info(
        f"[ODDS-API]{' '+tag if tag else ''} used={used} remaining={remaining} last_call_cost={last_cost}"
    )
    row = {
        "ts_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
        "endpoint": resp.request.path_url,
        "tag": tag,
        "status": resp.status_code,
        "used": used,
        "remaining": remaining,
        "last_cost": last_cost,
    }
    write_header = not os.path.exists(CALL_LOG_PATH)
    with open(CALL_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if write_header:
            w.writeheader()
        w.writerow(row)

def http_get_json(session: requests.Session, url: str, params: Dict, tag: str = "") -> dict:
    """GET ``url`` with ``params`` and return the parsed JSON.

    Retries up to three times on 5xx responses.
    """
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
def list_upcoming_events(
    api_key: str, sport_key: str = NFL_SPORT_KEY, days_from: int = 7
) -> List[dict]:
    """Return upcoming events from the Odds API."""
    session = _requests_session()
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events"
    params = {"apiKey": api_key, "daysFrom": days_from}
    return http_get_json(session, url, params, tag="events_free")

def get_event_odds(
    api_key: str, event_id: str, regions: str, odds_format: str, markets: str
) -> dict:
    """Fetch odds for ``event_id`` and return JSON data."""
    session = _requests_session()
    url = f"https://api.the-odds-api.com/v4/sports/{NFL_SPORT_KEY}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "oddsFormat": odds_format,
        "markets": markets,
    }
    tag = f"event={event_id} markets={markets}"
    return http_get_json(session, url, params, tag=tag)

def estimate_credits(num_events: int, markets: List[str], regions: str = "us") -> int:
    """Estimate Odds API credits consumed by a run."""
    n_markets = len(markets)
    n_regions = len(regions.split(",")) if isinstance(regions, str) else 1
    return num_events * n_markets * n_regions

# -------------------- Name & market helpers --------------------
def normalize_name(n: str) -> str:
    """Lowercase and strip punctuation/whitespace from a name."""
    n = (n or "").lower().strip()
    n = re.sub(r"[.,'’]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n

DISCRETE_KEYS = {
    "player_receptions",
    "player_pass_tds",
    "player_rush_tds",
    "player_reception_tds",
    "player_pass_attempts",
    "player_pass_completions",
    "player_interceptions",
    "player_pass_interceptions",
    "player_rush_attempts",
    "player_pass_rush_reception_tds",
}

TEAM_ALIASES = {
    "ARIZONACARDINALS": ["ARI", "ARIZONA", "ARIZONA CARDINALS"],
    "ATLANTAFALCONS": ["ATL", "ATLANTA", "ATLANTA FALCONS"],
    "BALTIMORERAVENS": ["BAL", "BALTIMORE", "BALTIMORE RAVENS"],
    "BUFFALOBILLS": ["BUF", "BUFFALO", "BUFFALO BILLS"],
    "CAROLINAPANTHERS": ["CAR", "CAROLINA", "CAROLINA PANTHERS"],
    "CHICAGOBEARS": ["CHI", "CHICAGO", "CHICAGO BEARS"],
    "CINCINNATIBENGALS": ["CIN", "CINCINNATI", "CINCINNATI BENGALS"],
    "CLEVELANDBROWNS": ["CLE", "CLEVELAND", "CLEVELAND BROWNS"],
    "DALLASCOWBOYS": ["DAL", "DALLAS", "DALLAS COWBOYS"],
    "DENVERBRONCOS": ["DEN", "DENVER", "DENVER BRONCOS"],
    "DETROITLIONS": ["DET", "DETROIT", "DETROIT LIONS"],
    "GREENBAYPACKERS": ["GB", "GBP", "GB PACKERS", "GREEN BAY", "GREEN BAY PACKERS"],
    "HOUSTONTEXANS": ["HOU", "HOUSTON", "HOUSTON TEXANS"],
    "INDIANAPOLISCOLTS": ["IND", "INDIANAPOLIS", "INDIANAPOLIS COLTS"],
    "JACKSONVILLEJAGUARS": ["JAX", "JAC", "JACKSONVILLE", "JACKSONVILLE JAGUARS"],
    "KANSASCITYCHIEFS": ["KC", "KAN", "KANSAS CITY", "KANSAS CITY CHIEFS"],
    "LASVEGASRAIDERS": ["LV", "LVR", "LAS VEGAS", "LAS VEGAS RAIDERS"],
    "LOSANGELESRAMS": ["LAR", "LA RAMS", "LOS ANGELES", "LOS ANGELES RAMS"],
    "LOSANGELESCHARGERS": ["LAC", "LA CHARGERS", "LOS ANGELES CHARGERS"],
    "MIAMIDOLPHINS": ["MIA", "MIAMI", "MIAMI DOLPHINS"],
    "MINNESOTAVIKINGS": ["MIN", "MINNESOTA", "MINNESOTA VIKINGS"],
    "NEWENGLANDPATRIOTS": ["NE", "NENG", "NEW ENGLAND", "NEW ENGLAND PATRIOTS"],
    "NEWORLEANSSAINTS": ["NO", "NOR", "NEW ORLEANS", "NEW ORLEANS SAINTS"],
    "NEWYORKGIANTS": ["NYG", "N.Y.G", "NEW YORK GIANTS", "NY GIANTS"],
    "NEWYORKJETS": ["NYJ", "N.Y.J", "NEW YORK JETS", "NY JETS"],
    "PHILADELPHIAEAGLES": ["PHI", "PHILADELPHIA", "PHILADELPHIA EAGLES"],
    "PITTSBURGHSTEELERS": ["PIT", "PITTSBURGH", "PITTSBURGH STEELERS"],
    "SANFRANCISCO49ERS": ["SF", "SFO", "SAN FRANCISCO", "SAN FRANCISCO 49ERS", "49ERS"],
    "SEATTLESEAHAWKS": ["SEA", "SEATTLE", "SEATTLE SEAHAWKS"],
    "TAMPABAYBUCCANEERS": ["TB", "TAMPA", "TAMPA BAY", "TAMPA BAY BUCCANEERS"],
    "TENNESSEETITANS": ["TEN", "TENNESSEE", "TENNESSEE TITANS"],
    "WASHINGTONCOMMANDERS": [
        "WAS",
        "WSH",
        "WASHINGTON",
        "WASHINGTON COMMANDERS",
        "WASHINGTON FOOTBALL TEAM",
    ],
}

def _clean_team_token(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


TEAM_CANONICAL_MAP = {}
for canonical, aliases in TEAM_ALIASES.items():
    TEAM_CANONICAL_MAP[_clean_team_token(canonical)] = canonical
    for alias in aliases:
        TEAM_CANONICAL_MAP[_clean_team_token(alias)] = canonical


def canonical_team(name: str) -> str:
    token = _clean_team_token(name)
    if not token:
        return ""
    return TEAM_CANONICAL_MAP.get(token, token)

def is_discrete_market(market_key: str) -> bool:
    """Whether ``market_key`` represents a discrete outcome."""
    return market_key in DISCRETE_KEYS

def sanity_line_ok(market_key: str, line: float) -> bool:
    """Quick sanity check that a book's line is non-negative."""
    if line is None:
        return False
    try:
        return float(line) >= 0.0
    except Exception:
        return False

# -------------------- Variance blending --------------------
def make_variance_blend(
    row: pd.Series, market_key: str, sigma_cfg: dict, alpha: float
) -> float:
    """Blend player-specific and positional variance estimates."""
    pos = str(row.get("pos") or row.get("position") or "").upper()
    src_sd = None
    for cand in [f"{market_key}_sd", market_key.replace("player_", "") + "_sd"]:
        if cand in row and pd.notna(row[cand]):
            try:
                src_sd = float(row[cand])
                break
            except Exception:
                pass
    base_sigma = float(sigma_cfg.get(pos, {}).get(market_key, 25.0))
    if src_sd is None:
        return base_sigma
    return math.sqrt(alpha * (src_sd**2) + (1 - alpha) * (base_sigma**2))

# -------------------- Best offer (robust name + fallback books) --------------------
def _player_name_match(desc: str, player_norm: str) -> bool:
    """Heuristic match between Odds API description and projection name."""
    if not desc:
        return False
    dn = normalize_name(desc)
    if dn == player_norm:
        return True
    pt = player_norm.split()
    dt = dn.split()
    if not pt:
        return False
    last = pt[-1]
    # Odds API sometimes collapses names like "B.Robinson" into a single token
    # without spaces.  Accept tokens that contain the last name instead of
    # requiring an exact token match so those entries still qualify.
    last_ok = any(last == t or last in t or t in last for t in dt)
    if not last_ok and last:
        dn_compact = dn.replace(" ", "")
        last_ok = last in dn_compact
    if len(pt) >= 2:
        first = pt[0]
        initials = {t[0] for t in dt if t}
        first_ok = (first in dt) or (first and first[0] in initials)
        if not first_ok and first:
            # Handle collapsed variants like "jallen".
            combo = (first[0] + last) if last else ""
            if combo and combo in dn.replace(" ", ""):
                first_ok = True
        return last_ok and first_ok
    target = pt[0]
    return any(target == t or target in t or t in target for t in dt)

def best_offer_for_player(
    event_json: dict, player_name: str, market_key: str, side: str, target_books: set
):
    """Find the best line/price for a player and market."""
    player_norm = normalize_name(player_name)

    def _search(allowed_books: Optional[set]) -> Optional[Tuple[str, float, int]]:
        best_local: Optional[Tuple[str, float, int]] = None
        for bm in (event_json or {}).get("bookmakers", []) or []:
            bk = bm.get("key", "")
            if allowed_books is not None and allowed_books and bk not in allowed_books:
                continue
            for mk in bm.get("markets", []) or []:
                if mk.get("key") != market_key:
                    continue
                for outc in mk.get("outcomes", []) or []:
                    pstr = next(
                        (
                            outc.get(field)
                            for field in ("participant", "player", "player_name", "name")
                            if outc.get(field)
                        ),
                        None,
                    )
                    if not pstr:
                        pstr = outc.get("description") or ""
                    if not _player_name_match(pstr, player_norm):
                        continue
                    line = outc.get("point")
                    price = outc.get("price")
                    if line is None or price is None:
                        continue
                    try:
                        line = float(line)
                        price = int(price)
                    except Exception:
                        continue
                    if not sanity_line_ok(market_key, line):
                        continue
                    cand = (bk, line, price)
                    if best_local is None:
                        best_local = cand
                    else:
                        if side == "OVER":
                            if cand[1] < best_local[1] or (
                                cand[1] == best_local[1] and cand[2] > best_local[2]
                            ):
                                best_local = cand
                        else:
                            if cand[1] > best_local[1] or (
                                cand[1] == best_local[1] and cand[2] > best_local[2]
                            ):
                                best_local = cand
        return best_local

    best = _search(target_books if target_books else None)
    if best is None and target_books:
        best = _search(None)
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
    regions_env = os.environ.get("REGIONS", "").strip()
    regions = regions_env if regions_env else cfg.get("regions", "us,us2")
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
    odds_levels_cfg = cfg.get("odds_levels", [-120, -110, 100])
    odds_levels: List[int] = []
    for lvl in (odds_levels_cfg or []):
        try:
            odds_levels.append(int(float(lvl)))
        except Exception:
            continue
    if not odds_levels:
        odds_levels = [-120, -110, 100]
    max_juice = cfg.get("max_juice")
    if max_juice is not None and str(max_juice).strip() != "":
        try:
            max_juice = int(float(max_juice))
        except Exception:
            logging.warning("Invalid max_juice value %s; ignoring", max_juice)
            max_juice = None
    else:
        max_juice = None
    top_n = cfg.get("top_n")
    try:
        top_n = int(top_n)
    except Exception:
        top_n = 0
    odds_format = cfg.get("odds_format", "american")

    # Diagnostics
    diag = {
        "regions": regions,
        "profile": profile,
        "markets_requested": markets_list,
        "odds_levels": odds_levels,
        "max_juice": max_juice,
        "projections_cols": list(projections.columns),
        "players_in_projections": int(len(projections)),
        "team_filter_disabled": (os.environ.get("NO_TEAM_FILTER", "0") == "1"),
        "events": 0,
        "events_used": 0,
        "reasons": {},
    }
    def bump(reason: str):
        diag["reasons"][reason] = int(diag["reasons"].get(reason, 0)) + 1

    events = list_upcoming_events(api_key, days_from=days_from)
    num_events = len(events or [])
    diag["events"] = num_events
    est = estimate_credits(num_events, markets_list, regions=regions)
    logging.info(f"[BUDGET] events={num_events}; markets={len(markets_list)}; estimated_credits≈{est}")

    if est > max_calls and num_events > 0:
        logging.warning(f"[BUDGET] est {est} > max_calls {max_calls}. Trimming markets.")
        keep = max(1, max_calls // num_events)
        markets_list = markets_list[:keep]
        logging.info(f"[BUDGET] trimmed markets to {len(markets_list)}")
        diag["markets_trimmed"] = markets_list

    event_map = {e["id"]: e for e in (events or [])}

    rows = []
    for event_id, ev in event_map.items():
        if not markets_list:
            break
        markets_csv = ",".join(markets_list)
        ev_json = get_event_odds(
            api_key,
            event_id,
            regions=regions,
            odds_format=odds_format,
            markets=markets_csv,
        )

        if not ev_json or not ev_json.get("bookmakers"):
            bump("no_bookmakers_for_event")
            continue
        diag["events_used"] += 1

        home_raw = ev.get("home_team") or ""
        away_raw = ev.get("away_team") or ""
        home = home_raw.upper()
        away = away_raw.upper()
        home_canon = canonical_team(home_raw)
        away_canon = canonical_team(away_raw)

        use_all = os.environ.get("NO_TEAM_FILTER", "0") == "1"

        def team_matches(t: str) -> bool:
            if not t:
                return False
            canon = canonical_team(t)
            if canon and (canon == home_canon or canon == away_canon):
                return True
            T = (t or "").upper()
            return (home in T) or (away in T) or (T in home) or (T in away)

        if use_all:
            df_ev = projections
        else:
            df_ev = (
                projections[projections["team"].apply(team_matches)]
                if {"team"}.issubset(projections.columns)
                else projections
            )
            if df_ev.empty:
                bump("team_filter_yielded_empty")
                df_ev = projections

        for _, r in df_ev.iterrows():
            player = r.get("player") or r.get("name") or ""
            if not player:
                bump("missing_player_name")
                continue
            for mkey in markets_list:
                if mkey not in r:
                    bump(f"missing_market_col::{mkey}")
                    continue
                try:
                    mu = float(r[mkey])
                except Exception:
                    bump(f"bad_projection_value::{mkey}")
                    continue
                if pd.isna(r[mkey]):
                    bump(f"missing_projection_value::{mkey}")
                    continue
                sd = make_variance_blend(r, mkey, sigma_defaults, alpha)
                for side in ("OVER", "UNDER"):
                    offer = best_offer_for_player(ev_json, player, mkey, side, target_books)
                    if not offer:
                        bump(f"no_offer::{mkey}::{side}")
                        continue
                    best_book, book_line, book_odds = offer
                    win_prob = prob_over(book_line, mu, sd, is_discrete=is_discrete_market(mkey))
                    if side == "UNDER":
                        win_prob = 1 - win_prob
                    ev_now = ev_per_unit(win_prob, book_odds)
                    if max_juice is not None and book_odds < max_juice:
                        bump(f"price_exceeds_max_juice::{mkey}")
                        continue

                    extra_evs = {
                        f"ev@{lvl}": round(ev_per_unit(win_prob, lvl), 4)
                        for lvl in odds_levels
                    }
                    playable = "YES" if ev_now > 0 else "NO"

                    unit_size = bankroll * unit_pct
                    stake_u = 0.0
                    for band in sorted(stake_bands, key=lambda x: x["min_ev"], reverse=True):
                        if ev_now >= band["min_ev"]:
                            stake_u = band["stake_u"]; break
                    stake_dollars = round(unit_size * stake_u, 2)

                    row = {
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
                        "playable": playable,
                        "stake_u": stake_u,
                        "stake_$": stake_dollars,
                        "event_id": event_id,
                        "home_team": home,
                        "away_team": away,
                    }
                    row.update(extra_evs)
                    rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values(["ev_per_unit", "win_prob"], ascending=[False, False], inplace=True, kind="mergesort")
        df.reset_index(drop=True, inplace=True)
        if top_n and top_n > 0:
            df = df.head(top_n).copy()

    # Write diagnostics
    os.makedirs("artifacts", exist_ok=True)
    with open("artifacts/scan_debug.json", "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2)
    with open("artifacts/scan_summary.txt", "w", encoding="utf-8") as f:
        f.write(f"events={diag['events']} used={diag['events_used']}\n")
        f.write(f"markets={markets_list}\n")
        f.write(f"odds_levels={odds_levels}\n")
        f.write(f"max_juice={max_juice}\n")
        if top_n and top_n > 0:
            f.write(f"top_n={top_n}\n")
        f.write(f"players={diag['players_in_projections']}\n")
        f.write("reasons:\n")
        for k, v in sorted(diag["reasons"].items(), key=lambda kv: (-kv[1], kv[0])):
            f.write(f"  {k}: {v}\n")
        missing_proj = {
            reason.partition("::")[2]: count
            for reason, count in diag["reasons"].items()
            if reason.startswith("missing_projection_value::")
        }
        if missing_proj:
            f.write("missing_projection_values:\n")
            for market, count in sorted(missing_proj.items(), key=lambda kv: (-kv[1], kv[0])):
                f.write(f"  {market}: {count}\n")

    return df
