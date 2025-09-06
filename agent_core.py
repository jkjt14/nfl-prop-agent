
import os, re, math, requests, pandas as pd, numpy as np
from typing import List, Dict, Tuple, Optional
from math import erf, sqrt
from scipy.stats import norm

NFL_SPORT_KEY = "americanfootball_nfl"

def american_to_implied_p(odds: int) -> float:
    return (-odds)/((-odds)+100) if odds < 0 else 100/(odds+100)

def normal_cdf(x, mu, sd):
    if sd <= 0: 
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / sd
    return 0.5*(1 + erf(z / sqrt(2)))

def prob_over(line, mu, sd, is_discrete=False):
    # discrete continuity correction for receptions-like counts
    return 1 - normal_cdf(line + (0.5 if is_discrete else 0.0), mu, sd)

def ev_per_unit(p, american_odds):
    b = (100/abs(american_odds)) if american_odds < 0 else (american_odds/100)
    return p*b - (1-p)*1

def kelly_fraction(p, american_odds):
    b = (100/abs(american_odds)) if american_odds < 0 else (american_odds/100)
    if b <= 0: return 0.0
    q = 1 - p
    return max(0.0, (b*p - q)/b)

def http_get(url: str, params: Dict) -> dict:
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def list_upcoming_events(api_key: str, sport_key: str = NFL_SPORT_KEY, days_from: int = 10) -> List[dict]:
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events"
    return http_get(url, {"apiKey": api_key, "daysFrom": days_from})

def get_event_odds(api_key: str, event_id: str, regions: str, odds_format: str, markets: List[str]) -> dict:
    url = f"https://api.the-odds-api.com/v4/sports/{NFL_SPORT_KEY}/events/{event_id}/odds"
    params = {"apiKey": api_key, "regions": regions, "oddsFormat": odds_format, "markets": ",".join(markets)}
    return http_get(url, params)

def normalize_name(n: str) -> str:
    n = (n or "").lower().strip()
    n = re.sub(r"[.,']", "", n)
    n = re.sub(r"\s+", " ", n)
    return n

def make_variance_blend(row, market_key, sigma_cfg: dict, alpha: float) -> float:
    pos = str(row.get("position","")).upper()
    # attempt to read *_sd or market-specific sd column
    src_sd = None
    # heuristics to find any sd column for this market
    for cand in [f"{market_key}_sd", market_key.replace("player_","")+"_sd"]:
        if cand in row and pd.notna(row[cand]):
            try:
                src_sd = float(row[cand])
                break
            except:
                pass
    base_sigma = float(sigma_cfg.get(pos, {}).get(market_key, 25.0))
    if src_sd is None:
        return base_sigma
    # variance blend
    return math.sqrt(alpha*(src_sd**2) + (1-alpha)*(base_sigma**2))

def best_offer_for_player(event_json: dict, player_name: str, market_key: str, side: str, target_books: set):
    player_norm = normalize_name(player_name)
    best = None  # (book, line, price)
    for bm in event_json.get("bookmakers", []):
        bk = bm.get("key","")
        if bk not in target_books: 
            continue
        for mk in bm.get("markets", []):
            if mk.get("key") != market_key:
                continue
            for outc in mk.get("outcomes", []):
                desc = normalize_name(outc.get("description") or outc.get("name") or "")
                if player_norm not in desc: 
                    continue
                line = outc.get("point")
                price = outc.get("price")
                if line is None or price is None: 
                    continue
                cand = (bk, float(line), int(price))
                if best is None:
                    best = cand
                else:
                    # OVER wants the lowest line, then better odds; UNDER wants highest line, then better odds
                    if side == "OVER":
                        if cand[1] < best[1] or (cand[1] == best[1] and cand[2] > best[2]):
                            best = cand
                    else:
                        if cand[1] > best[1] or (cand[1] == best[1] and cand[2] > best[2]):
                            best = cand
    return best

def stake_units(ev_value: float, bands: List[dict]) -> float:
    # Sort high to low by min_ev
    bands = sorted(bands, key=lambda x: x["min_ev"], reverse=True)
    for b in bands:
        if ev_value >= b["min_ev"]:
            return b["stake_u"]
    return 0.0
