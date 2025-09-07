#!/usr/bin/env python3
import os, sys, argparse, logging, json
import pandas as pd
import yaml

from agent_core import scan_edges  # uses the updated file you pasted earlier

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)

def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def main():
    p = argparse.ArgumentParser(description="NFL prop agent: scan & alert")
    p.add_argument("--projections", "-p", default=os.getenv("PROJECTIONS_CSV", "data/projections.csv"),
                   help="CSV with projections (player, team, pos, market columns)")
    p.add_argument("--config", "-c", default=os.getenv("AGENT_CONFIG", "agent_config.yaml"),
                   help="YAML config (books, markets, bankroll, etc.)")
    p.add_argument("--profile", default=os.getenv("PROFILE", "base"),
                   help="cfg.markets profile to use (base/heavy/etc.)")
    p.add_argument("--days-from", type=int, default=int(os.getenv("DAYS_FROM", "7")),
                   help="OdssAPI events daysFrom window")
    p.add_argument("--max-calls", type=int, default=int(os.getenv("MAX_CALLS", "1000")),
                   help="soft cap on OddsAPI credit usage (events*markets)")
    p.add_argument("--top", type=int, default=int(os.getenv("TOP_PRINT", "10")),
                   help="print top N edges")
    args = p.parse_args()

    api_key = os.getenv("ODDS_API_KEY", "").strip()
    if not api_key:
        logging.error("Missing ODDS_API_KEY (env var or GitHub Secret).")
        sys.exit(2)

    if not os.path.exists(args.projections):
        logging.error(f"Projections CSV not found: {args.projections}")
        sys.exit(2)

    if not os.path.exists(args.config):
        logging.error(f"Config YAML not found: {args.config}")
        sys.exit(2)

    logging.info(f"Loading projections: {args.projections}")
    proj = pd.read_csv(args.projections)

    logging.info(f"Loading config: {args.config}")
    cfg = load_cfg(args.config)

    # Run the scan (this will also save CSV & send alerts if enabled)
    df_edges = scan_edges(
        projections=proj,
        cfg=cfg,
        api_key=api_key,
        days_from=args.days_from,
        profile=args.profile,
        max_calls=args.max_calls,
    )

    if df_edges.empty:
        print("No edges.")
        return

    # Pretty print top N advice lines to stdout
    print("\nTop edges:")
    for line in df_edges["advice"].head(args.top):
        print("•", line)

    # Also write a simple JSON for frontends/Pages if you want
    if os.getenv("EDGES_JSON_OUT"):
        out_json = os.getenv("EDGES_JSON_OUT")
        df_edges.head(200).to_json(out_json, orient="records", indent=2)
        logging.info(f"Wrote JSON: {out_json}")

if __name__ == "__main__":
    main()
