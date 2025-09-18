"""Slack alert helpers for NFL Prop Agent."""

import json
import logging
import os
from typing import Optional

import requests
import pandas as pd

def _fmt_pct(x: float) -> str:
    """Format a probability/EV float as a percentage string."""
    return f"{x*100:.1f}%"

def _market_readable(mkey: str) -> str:
    """Return a human-friendly name for ``mkey``."""
    m = {
        "player_pass_yds": "passing yards",
        "player_rush_yds": "rushing yards",
        "player_reception_yds": "receiving yards",
        "player_receptions": "receptions",
        "player_pass_tds": "pass TDs",
        "player_rush_tds": "rush TDs",
        "player_reception_tds": "rec TDs",
        "player_interceptions": "def INTs",
        "player_pass_interceptions": "pass INTs",
        "player_pass_completions": "pass completions",
        "player_pass_attempts": "pass attempts",
        "player_pass_longest_completion": "longest completion",
        "player_longest_reception": "longest reception",
        "player_reception_longest": "longest reception",
        "player_longest_rush": "longest rush",
        "player_rush_longest": "longest rush",
        "player_rush_attempts": "rush attempts",
        "player_pass_rush_reception_yds": "pass+rush+rec yards",
        "player_pass_rush_reception_tds": "pass+rush+rec TDs",
    }
    return m.get(mkey, mkey.replace("_", " "))

def format_advice(df: pd.DataFrame, threshold: float) -> str:
    """Create a multi-line Slack message summarizing high-EV edges."""
    if df is None or df.empty:
        return "No edges found."
    lines = []
    for _, r in df.iterrows():
        if r["ev_per_unit"] < threshold:
            continue
        fallback_note = ""
        fb_book = r.get("fallback_book")
        if fb_book and isinstance(fb_book, str):
            fb_line = r.get("fallback_line")
            fb_odds = r.get("fallback_odds")
            line_str = "NA"
            if not pd.isna(fb_line):
                line_str = f"{fb_line:g}" if isinstance(fb_line, (int, float)) else str(fb_line)
            odds_str = "NA" if pd.isna(fb_odds) else str(int(fb_odds))
            fallback_note = f" (alt: {fb_book} {odds_str} @ {line_str})"
        lines.append(
            f"{r['player']} {r['side']} {r['book_line']} {_market_readable(r['market_key'])} — "
            f"{r['book_odds']} ({r['best_book']}) | EV {_fmt_pct(r['ev_per_unit'])} | {r['stake_u']}u{fallback_note}"
        )
    return "\n".join(lines[:25]) if lines else "No edges ≥ threshold."

def alert_edges(
    df: pd.DataFrame, threshold_ev: float = 0.06, webhook: Optional[str] = None
) -> None:
    """Post formatted edges to Slack or write a failure artifact."""
    os.makedirs("artifacts", exist_ok=True)
    webhook = webhook or os.environ.get("SLACK_WEBHOOK", "")
    msg = "*NFL Edges*\n" + format_advice(df, threshold_ev)
    if not webhook:
        with open("artifacts/slack_failed.txt", "w", encoding="utf-8") as f:
            f.write("No SLACK_WEBHOOK set\n" + msg)
        logging.warning("[SLACK] Webhook not set; wrote artifacts/slack_failed.txt")
        return

    payload = json.dumps({"text": msg})
    for attempt in range(3):
        try:
            resp = requests.post(
                webhook,
                data=payload,
                headers={"Content-Type": "application/json"},
                timeout=12,
            )
            if resp.status_code < 400:
                logging.info("[SLACK] Posted advice.")
                return
            logging.error("[SLACK] HTTP %s", resp.status_code)
        except Exception as e:  # pragma: no cover - network errors
            logging.exception("[SLACK] Exception posting to webhook: %s", e)
        if attempt < 2:
            import time

            time.sleep(2 * (attempt + 1))

    with open("artifacts/slack_failed.txt", "w", encoding="utf-8") as f:
        f.write(msg)
    logging.error("[SLACK] Failed after retries; wrote artifacts/slack_failed.txt")
