# alerts.py
import os, json, math
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
    lines = []
    for _, r in df.iterrows():
        if r["ev_per_unit"] < threshold:
            continue
        evp = _fmt_pct(r["ev_per_unit"])
        line = (
            f"{r['player']} {r['side']} {r['book_line']} {_market_readable(r['market_key'])} — "
            f"{r['book_odds']} ({r['best_book']}) | EV {evp} | {r['stake_u']}u"
        )
        lines.append(line)
    if not lines:
        return "No edges ≥ threshold."
    return "\n".join(lines[:25])

def alert_edges(df: pd.DataFrame, threshold_ev: float = 0.06, webhook: Optional[str] = None) -> None:
    webhook = webhook or os.environ.get("SLACK_WEBHOOK", "")
    msg = format_advice(df, threshold_ev)
    if not webhook:
        print("[SLACK] Webhook not set; printing instead:\n" + msg)
        return
    payload = {"text": "*NFL Edges*\n" + msg}
    try:
        resp = requests.post(webhook, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=12)
        if resp.status_code >= 400:
            print(f"[SLACK] Error {resp.status_code}: {resp.text}")
        else:
            print("[SLACK] Posted advice.")
    except Exception as e:
        print(f"[SLACK] Exception: {e}")
