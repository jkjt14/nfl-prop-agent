import os, json, argparse, pandas as pd
from agent_core import scan_edges

DEFAULT_CFG = {
    "regions": "us",
    "target_books": ["fanduel","espnbet","betmgm","caesars","fanatics","ballybet"],  # DraftKings excluded
    "blend_alpha": 0.35,
    "markets": {
        "base": [
            "player_pass_yds","player_rush_yds","player_reception_yds","player_receptions",
            "player_pass_tds","player_rush_tds","player_reception_tds"
        ],
        "heavy": [
            "player_pass_yds","player_rush_yds","player_reception_yds","player_receptions",
            "player_pass_tds","player_rush_tds","player_reception_tds",
            "player_pass_attempts","player_pass_completions","player_interceptions",
            "player_longest_reception","player_longest_rush"
        ]
    },
    "sigma_defaults": {
        "QB": {"player_pass_yds": 60, "player_pass_tds": 0.75, "player_interceptions": 0.5,
               "player_pass_attempts": 6, "player_pass_completions": 5},
        "RB": {"player_rush_yds": 20, "player_rush_tds": 0.5, "player_receptions": 1.6,
               "player_reception_yds": 18, "player_longest_rush": 7},
        "WR": {"player_receptions": 2.0, "player_reception_yds": 30, "player_reception_tds": 0.5,
               "player_longest_reception": 8},
        "TE": {"player_receptions": 1.6, "player_reception_yds": 22, "player_reception_tds": 0.45,
               "player_longest_reception": 7}
    },
    "bankroll": 1000.0,
    "unit_pct": 0.01,  # 1u = $10
    "stake_bands": [
        {"min_ev": 0.08, "stake_u": 1.0},
        {"min_ev": 0.04, "stake_u": 0.5},
        {"min_ev": 0.02, "stake_u": 0.3}
    ]
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projections", required=True, help="Path to projections CSV")
    ap.add_argument("--out", required=True, help="Output CSV for edges")
    ap.add_argument("--days", type=int, default=7, help="Days ahead (events)")
    ap.add_argument("--profile", choices=["base","heavy"], default="base", help="Market profile")
    ap.add_argument("--max-calls", type=int, default=2000, help="Max estimated credits per run")
    ap.add_argument("--config", default="", help="Optional JSON or YAML config path")
    args = ap.parse_args()

    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        raise SystemExit("ODDS_API_KEY not set")

    df = pd.read_csv(args.projections)

    cfg = DEFAULT_CFG.copy()
    if args.config:
        if args.config.lower().endswith(".json"):
            import json as _json
            with open(args.config, "r", encoding="utf-8") as f:
                cfg.update(_json.load(f))
        else:
            try:
                import yaml
                with open(args.config, "r", encoding="utf-8") as f:
                    cfg.update(yaml.safe_load(f))
            except Exception:
                pass

    edges = scan_edges(
        df, cfg,
        api_key=api_key,
        days_from=args.days,
        profile=args.profile,
        max_calls=args.max_calls
    )
    edges.to_csv(args.out, index=False)
    print(f"Wrote {args.out} ({len(edges)} rows)")

if __name__ == "__main__":
    main()
