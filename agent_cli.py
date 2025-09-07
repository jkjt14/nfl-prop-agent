#!/usr/bin/env python3
import os, sys, json, logging
import pandas as pd

from agent_core import scan_edges

# Optional alerts; keep it lightweight (simple webhook)
def post_slack_blocks(webhook: str, blocks: list) -> None:
    import requests
    try:
        r = requests.post(webhook, json={"blocks": blocks}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.warning(f"Slack post failed: {e}")

def alert_edges(df: pd.DataFrame, webhook: str, threshold_ev: float = 0.06, batch: int = 10) -> int:
    if df.empty:
        return 0
    want = df[df["ev"] >= threshold_ev].copy()
    if want.empty:
        return 0

    # Build Slack blocks
    blocks = []
    blocks.append({"type": "header", "text": {"type": "plain_text", "text": f"Edges ≥ {threshold_ev*100:.0f}% EV"}})
    for _, s in want.head(batch).iterrows():
        line = f"{s['player']} {s['side']} {s['line']:.1f} {s['market_readable']}"
        odds = f"{int(s['price']):+d}"
        sub = f"{s['book']} {odds} • EV {s['ev']*100:.1f}% • stake {float(s['stake_u']):.1f}u"
        blocks.extend([
            {"type":"section","text":{"type":"mrkdwn","text":f"*{line}*"}},
            {"type":"context","elements":[{"type":"mrkdwn","text":sub}]},
            {"type":"divider"}
        ])
    post_slack_blocks(webhook, blocks)
    return len(want)

def load_cfg(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    # Minimal defaults if no config.json present
    return {
        "regions": "us",
        "target_books": [],  # empty = allow all
        "blend_alpha": 0.35,
        "markets": {
            "base": [
                "player_pass_yds","player_rush_yds","player_reception_yds","player_receptions"
            ]
        },
        "bankroll": 1000.0,
        "unit_pct": 0.01,
        "stake_bands": [
            {"min_ev": 0.08, "stake_u": 1.0},
            {"min_ev": 0.04, "stake_u": 0.5},
            {"min_ev": 0.02, "stake_u": 0.3},
        ],
        "sigma_defaults": {
            "QB": {"player_pass_yds": 45.0},
            "WR": {"player_reception_yds": 30.0, "player_receptions": 2.0},
            "RB": {"player_rush_yds": 20.0},
            "TE": {"player_reception_yds": 25.0, "player_receptions": 1.8}
        }
    }

def main() -> int:
    logging.basicConfig(level=os.environ.get("LOGLEVEL","INFO"), format="%(asctime)s %(levelname)s %(message)s")

    api_key = os.environ.get("ODDS_API_KEY","").strip()
    if not api_key:
        logging.error("Missing ODDS_API_KEY env.")
        return 2

    # projections
    proj_path = os.environ.get("PROJECTIONS_PATH", "data/projections.csv")
    if not os.path.exists(proj_path):
        # fallbacks people often use
        for alt in ["data/raw_stats_current.csv", "artifacts/projections.csv"]:
            if os.path.exists(alt):
                proj_path = alt
                break
    if not os.path.exists(proj_path):
        logging.error(f"Projections file not found: {proj_path}")
        return 2

    df_proj = pd.read_csv(proj_path)
    logging.info(f"Loaded projections: {len(df_proj):,} rows, {len(df_proj.columns)} cols from {proj_path}")

    cfg_path = os.environ.get("AGENT_CFG", "config.json")
    cfg = load_cfg(cfg_path)
    profile = os.environ.get("MARKETS_PROFILE","base")
    days_from = int(os.environ.get("DAYS_FROM","7"))
    max_calls = int(os.environ.get("MAX_CALLS","1000"))

    # Scan
    df_edges = scan_edges(
        projections=df_proj,
        cfg=cfg,
        api_key=api_key,
        days_from=days_from,
        profile=profile,
        max_calls=max_calls,
    )

    # Outputs
    os.makedirs("artifacts", exist_ok=True)
    if df_edges.empty:
        logging.info("No offers were found across selected markets/books.")
        with open("artifacts/advice.txt","w",encoding="utf-8") as f:
            f.write("No edges found.\n")
        return 0

    df_edges.to_csv("artifacts/edges.csv", index=False)
    logging.info(f"Computed {len(df_edges):,} edges. Top 10 advice lines:")
    top = df_edges.head(10)
    advice_lines = [f"- {s}" for s in top["advice"].tolist()]
    print("\n".join(advice_lines))
    with open("artifacts/advice.txt","w",encoding="utf-8") as f:
        f.write("\n".join(advice_lines) + "\n")

    # Slack alerts
    threshold = float(os.environ.get("EDGE_THRESHOLD","0.06"))
    webhook = os.environ.get("SLACK_WEBHOOK","").strip()
    if webhook:
        n = alert_edges(df_edges, webhook=webhook, threshold_ev=threshold)
        logging.info(f"Slack alert edges ≥ {threshold:.3f}: {n}")
    else:
        logging.info("SLACK_WEBHOOK not set; skipping Slack alerts.")

    return 0

if __name__ == "__main__":
    sys.exit(main())
