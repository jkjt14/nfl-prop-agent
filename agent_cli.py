
#!/usr/bin/env python3
import os, sys, json, argparse, math, pandas as pd, numpy as np
from typing import List, Dict
import yaml
from agent_core import (list_upcoming_events, get_event_odds, best_offer_for_player,
                        make_variance_blend, prob_over, ev_per_unit, american_to_implied_p,
                        stake_units)

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # env var substitution
    for k in ["ODDS_API_KEY"]:
        raw = raw.replace("${"+k+"}", os.environ.get(k, ""))
    return yaml.safe_load(raw)

def infer_discrete(market_key: str) -> bool:
    return "receptions" in market_key.lower() or market_key.lower().endswith("_attempts") or market_key.lower().endswith("_completions")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="agent_config.yaml")
    ap.add_argument("--projections", required=True, help="Path to your projections CSV")
    ap.add_argument("--out", default="edges_bestbook.csv")
    args = ap.parse_args()

    cfg = load_config(args.config)
    api_key = cfg.get("odds_api_key") or os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ERROR: Missing ODDS_API_KEY. Set env or config.")
        sys.exit(2)

    df = pd.read_csv(args.projections)
    events = list_upcoming_events(api_key, cfg.get("sport_key","americanfootball_nfl"), days_from=10)

    rows = []
    target_books = set(cfg["target_books"])
    for ev in events:
        ev_odds = get_event_odds(api_key, ev["id"], cfg["regions"], cfg["odds_format"], cfg["markets"])
        for idx, r in df.iterrows():
            name = r.get("player")
            team = r.get("team")
            pos  = r.get("position")
            # For every market listed in config, try both OVER and UNDER
            for mkey in cfg["markets"]:
                # try to find a mean in projections matching this market
                # accept both "player_pass_yds" column or simplified like "pass_yds"
                simple_col = mkey.replace("player_","")
                mu = None
                if mkey in df.columns and not pd.isna(r[mkey]):
                    mu = float(r[mkey])
                elif simple_col in df.columns and not pd.isna(r[simple_col]):
                    mu = float(r[simple_col])
                if mu is None:
                    continue
                sd = make_variance_blend(r, mkey, cfg["outcome_sigma"], float(cfg.get("blend_alpha",0.35)))
                for side in ["OVER","UNDER"]:
                    best = best_offer_for_player(ev_odds, name, mkey, side, target_books)
                    if not best: 
                        continue
                    book, line, price = best
                    is_disc = infer_discrete(mkey)
                    p = prob_over(line if side=="OVER" else (line - (0.5 if is_disc else 0.0)), mu, sd, is_discrete=is_disc) if side=="OVER" else 1 - prob_over(line, mu, sd, is_discrete=is_disc)
                    ev_actual = ev_per_unit(p, price)
                    row = {
                        "player": name, "team": team, "pos": pos,
                        "market_key": mkey, "side": side,
                        "proj_mean": round(mu,2), "model_sd": round(sd,2),
                        "best_book": book, "book_line": line, "book_odds": price,
                        "win_prob": round(p,4), "ev_per_unit": round(ev_actual,4),
                        "playable_at_thresh": "YES" if (price >= cfg["max_juice"] if price<0 else True) else "NO"
                    }
                    # EV snapshots at specified odds levels
                    for lvl in cfg["odds_levels"]:
                        row[f"ev@{lvl}"] = round(ev_per_unit(p, int(lvl)),4)
                    rows.append(row)

    if not rows:
        print("No matches found. Check markets, projections, or timing window.")
        sys.exit(1)

    out = pd.DataFrame(rows)
    # staking recommendations
    unit_value = float(cfg["bankroll"]) * float(cfg["unit_pct"])
    def stake_for_ev(ev):
        u = stake_units(ev, cfg["ev_bands"])
        return u, round(u*unit_value, 2)
    out["stake_u"], out["stake_$"] = zip(*out["ev_per_unit"].map(stake_for_ev))
    # sort by EV and keep top N (prefer playable)
    playable = out[out["playable_at_thresh"]=="YES"]
    top = (playable if len(playable)>=cfg["top_n"] else out).sort_values("ev_per_unit", ascending=False).head(cfg["top_n"])
    top.to_csv(args.out, index=False)
    print(f"Wrote {args.out} (top {len(top)})")

if __name__ == "__main__":
    main()
