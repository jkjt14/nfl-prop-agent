# alerts.py
import os, json
from typing import Optional
import requests
import pandas as pd

def _fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"

def _market_readable(mkey: str) -> str:
    m = {
        "player_pass_yds": "passing yards",
        "player_rush_yds": "rushing yards",
        "player_reception_yds": "receiving yards",
        "player_receptions": "receptions",
        "player_pass_tds": "pass TDs",
        "player_rush_tds": "rush TDs",
        "player_reception_tds": "rec TDs",
        "player_interceptions": "interceptions",
        "player_pass_completions": "pass completions",
        "player_pass_attempts": "pass attempts",
        "player_longest_reception": "longest reception",
        "player_longest_rush": "longest rush",
    }
    return m.get(mkey, mkey.replace("_", " "))

def format_advice(df: pd.DataFrame, threshold: float) -> str:
    if df is None or df.empty:
        return "No edges found."
    lines = []
    for _, r in df.iterrows():
        if r["ev_per_unit"] < threshold:
            continue
        lines.append(
            f"{r['player']} {r['side']} {r['book_line']} {_market_readable(r['market_key'])} — "
            f"{r['book_odds']} ({r['best_book']}) | EV {_fmt_pct(r['ev_per_unit'])} | {r['stake_u']}u"
        )
    return "\n".join(lines[:25]) if lines else "No edges ≥ threshold."

def alert_edges(df: pd.DataFrame, threshold_ev: float = 0.06, webhook: Optional[str] = None) -> None:
    os.makedirs("artifacts", exist_ok=True)
    webhook = webhook or os.environ.get("SLACK_WEBHOOK", "")
    msg = "*NFL Edges*\n" + format_advice(df, threshold_ev)
    if not webhook:
        with open("artifacts/slack_failed.txt", "w", encoding="utf-8") as f:
            f.write("No SLACK_WEBHOOK set\n" + msg)
        print("[SLACK] Webhook not set; wrote artifacts/slack_failed.txt")
        return
    try:
        resp = requests.post(webhook, data=json.dumps({"text": msg}),
                             headers={"Content-Type": "application/json"}, timeout=12)
        if resp.status_code >= 400:
            with open("artifacts/slack_failed.txt", "w", encoding="utf-8") as f:
                f.write(f"HTTP {resp.status_code}\n{resp.text}\n\n{msg}")
            print(f"[SLACK] Error {resp.status_code}; wrote artifacts/slack_failed.txt")
        else:
            print("[SLACK] Posted advice.")
    except Exception as e:
        with open("artifacts/slack_failed.txt", "w", encoding="utf-8") as f:
            f.write(f"Exception: {e}\n\n{msg}")
        print(f"[SLACK] Exception; wrote artifacts/slack_failed.txt")