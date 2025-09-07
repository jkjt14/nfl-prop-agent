#!/usr/bin/env python3
import os, sys, json, argparse, math
import pandas as pd
import yaml

from agent_core import scan_edges
from alerts import alert_edges

# ---------- helpers ----------

def fmt_american(odds: int) -> str:
    return f"{odds:+d}"

HUMAN_MARKET = {
    "player_pass_yds": "passing yards",
    "player_pass_tds": "pass TDs",
    "player_pass_attempts": "pass attempts",
    "player_pass_completions": "completions",
    "player_interceptions": "interceptions",

    "player_rush_yds": "rushing yards",
    "player_rush_tds": "rush TDs",
    "player_longest_rush": "longest rush",

    "player_receptions": "receptions",
    "player_receiving_yds": "receiving yards",
    "player_reception_tds": "receiving TDs",
    "player_longest_reception": "longest reception",
}

def market_readable(key: str) -> str:
    return HUMAN_MARKET.get(key, key.replace("_", " "))

def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_projections(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Projections CSV not found: {path}")
    df = pd.read_csv(path)
    # normalize common column names
    rename_map = {
        "position": "pos",
        "team_id": "team",
        "name": "player",
    }
    for a, b in rename_map.items():
        if a in df.columns and b not in df.columns:
            df[b] = df[a]
    # ensure required cols
    for c in ("player", "team", "pos"):
        if c not in df.columns:
            df[c] = ""
    return df

def advice_line(row) -> str:
    ev_pct = row["ev_per_unit"] * 100.0
    win_pct = row["win_prob"] * 100.0
    mk = market_readable(row["market_key"])
    odds_str = fmt_american(int(row["book_odds"]))
    line_str = f"{row['book_line']:.1f}".rstrip("0").rstrip(".")  # 268.5 -> "268.5", 0.0 -> "0"
    stake_u = row.get("stake_u", 0.0)
    stake_d = row.get("stake_$", 0.0)
    team = (row.get("team") or "").upper()
    pos = (row.get("pos") or "").upper()
    meta = " ".join(x for x in [team, pos] if x)
    meta = f" ({meta})" if meta else ""
    return (
        f"{row['player']}{meta} — {row['side']} {line_str} {mk} "
        f"@ {row['best_book']} {odds_str}  "
        f"(EV {ev_pct:+.1f}%, win {win_pct:.1f}%, stake {stake_u:g}u = ${stake_d:.2f})"
    )

def print_advice(df: pd.DataFrame, threshold_ev: float, top_n: int = 40) -> None:
    if df.empty:
        print("\nNo edges found.\n")
        return
    eligible = df[df["ev_per_unit"] >= threshold_ev].copy()
    if eligible.empty:
        print(f"\nNo edges ≥ {threshold_ev*100:.1f}% EV.\n")
        return
    eligible = eligible.sort_values(["ev_per_unit", "win_prob"], ascending=[False, False])
    print("\n=== Advice (edges meeting threshold) ===\n")
    for _, r in eligible.head(top_n).iterrows():
        print("• " + advice_line(r))
    print("")

# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(
        description="NFL prop agent: scan markets and print advice (+optional Slack alerts)."
    )
    parser.add_argument("--config", default=os.environ.get("AGENT_CONFIG", "agent_config.yaml"),
                        help="Path to YAML config (default: agent_config.yaml)")
    parser.add_argument("--profile", default=os.environ.get("PROFILE", "base"),
                        help="Markets profile key in config (default: base)")
    parser.add_argument("--days-from", default=os.environ.get("DAYS_FROM", "7"),
                        help="Days ahead to scan (default: 7)")
    parser.add_argument("--projections", default=os.environ.get("PROJECTIONS_CSV", "data/projections.csv"),
                        help="Path to projections CSV (default: data/projections.csv)")
    parser.add_argument("--edges-out", default=os.environ.get("EDGES_OUT", "artifacts/edges_bestbook.csv"),
                        help="CSV output path (default: artifacts/edges_bestbook.csv)")
    parser.add_argument("--edges-json-out", default=os.environ.get("EDGES_JSON_OUT", "artifacts/edges_bestbook.json"),
                        help="JSON output path for edges (default: artifacts/edges_bestbook.json)")
    parser.add_argument("--threshold", type=float,
                        default=float(os.environ.get("ALERT_MIN_EV", "0.06")),
                        help="EV threshold for advice/alerts, e.g. 0.06 for 6%% (default: 0.06)")
    parser.add_argument("--no-alerts", action="store_true",
                        help="Disable Slack alerts even if SLACK_WEBHOOK_URL is set / ENABLE_ALERTS=1")
    args = parser.parse_args()

    cfg = load_cfg(args.config)

    # Required: ODDS_API_KEY
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("ERROR: ODDS_API_KEY env var is required.", file=sys.stderr)
        sys.exit(2)

    # Load projections
    proj_path = args.projections
    df_proj = load_projections(proj_path)
    print(f"Loaded projections: {len(df_proj):,} rows, {len(df_proj.columns)} cols from {proj_path}")

    # Scan
    df_edges = scan_edges(
        projections=df_proj,
        cfg=cfg,
        api_key=api_key,
        days_from=int(str(args.days_from)),
        profile=args.profile,
        max_calls=int(cfg.get("max_calls", 2000)),
    )

    # Save outputs
    os.makedirs(os.path.dirname(args.edges_out), exist_ok=True)
    df_edges.to_csv(args.edges_out, index=False)
    with open(args.edges_json_out, "w", encoding="utf-8") as f:
        json.dump(df_edges.to_dict(orient="records"), f, indent=2)
    print(f"Saved edges to {args.edges_out} and {args.edges_json_out}")

    # Print clean advice
    print_advice(df_edges, threshold_ev=args.threshold)

    # Optional Slack alerts
    enable_alerts = (os.environ.get("ENABLE_ALERTS", "0") == "1") and (not args.no_alerts)
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if enable_alerts and webhook:
        try:
            alert_edges(df_edges, threshold_ev=args.threshold)
        except Exception as e:
            print(f"Slack alert error: {e}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    sys.exit(main())
