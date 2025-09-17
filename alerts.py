"""Slack alert helpers for NFL Prop Agent."""

import json
import logging
import os
import time
from typing import List, Optional

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
        lines.append(
            f"{r['player']} {r['side']} {r['book_line']} {_market_readable(r['market_key'])} — "
            f"{r['book_odds']} ({r['best_book']}) | EV {_fmt_pct(r['ev_per_unit'])} | {r['stake_u']}u"
        )
    return "\n".join(lines[:25]) if lines else "No edges ≥ threshold."

def _resolve_webhook(candidate: Optional[str]) -> str:
    """Return the preferred Slack webhook URL from config/env fallbacks."""

    if candidate and candidate.strip():
        return candidate.strip()
    # Prefer the new SLACK_WEBHOOK_URL variable but fall back to the legacy name
    # for backwards compatibility.
    env_hook = (
        os.environ.get("SLACK_WEBHOOK_URL")
        or os.environ.get("SLACK_WEBHOOK")
        or ""
    )
    return env_hook.strip()


def alert_edges(
    df: pd.DataFrame, threshold_ev: float = 0.06, webhook: Optional[str] = None
) -> None:
    """Post formatted edges to Slack or write a failure artifact."""
    os.makedirs("artifacts", exist_ok=True)
    webhook = _resolve_webhook(webhook)
    msg = "*NFL Edges*\n" + format_advice(df, threshold_ev)
    if not webhook:
        with open("artifacts/slack_failed.txt", "w", encoding="utf-8") as f:
            f.write("No SLACK_WEBHOOK_URL configured.\n\n")
            f.write(msg)
        logging.warning(
            "[SLACK] Webhook not set via config or SLACK_WEBHOOK_URL; wrote artifacts/slack_failed.txt"
        )
        return

    payload = json.dumps({"text": msg})
    errors: List[str] = []
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
            body = (resp.text or "").strip()
            detail = f"HTTP {resp.status_code}"
            if body:
                detail += f": {body}"
            errors.append(detail)
            logging.error("[SLACK] %s", detail)
        except Exception as e:  # pragma: no cover - network errors
            err_detail = f"Exception: {e}"
            errors.append(err_detail)
            logging.exception("[SLACK] Exception posting to webhook: %s", e)
        if attempt < 2:
            time.sleep(2 * (attempt + 1))

    with open("artifacts/slack_failed.txt", "w", encoding="utf-8") as f:
        if errors:
            f.write("\n".join(errors) + "\n\n")
        f.write(msg)
    logging.error("[SLACK] Failed after retries; wrote artifacts/slack_failed.txt")
