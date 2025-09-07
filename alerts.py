# alerts.py
import os, requests

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")

def post_slack(text, blocks=None):
    if not SLACK_WEBHOOK:
        # Quiet no-op if webhook not configured
        return False
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    r = requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
    r.raise_for_status()
    return True

def alert_edges(df, threshold_ev=0.06):
    """Send Slack messages for rows where df['ev'] >= threshold_ev.
    Expects columns: player, side, line, market_readable, book, price, ev, stake_u
    """
    # Make sure required columns exist
    required = {"player","side","line","market_readable","book","price","ev","stake_u"}
    missing = required - set(map(str, df.columns))
    if missing:
        raise ValueError(f"alert_edges: df is missing columns: {sorted(missing)}")

    hits = df[df["ev"] >= threshold_ev].copy()
    if hits.empty:
        return 0

    hits = hits.sort_values("ev", ascending=False).head(12)
    blocks = []
    for _, r in hits.iterrows():
        advice = (
            f"*{r['player']}* — *{r['side']} {r['line']} {r['market_readable']}*  "
            f"@ *{r['book']}* ({int(r['price']):+d})  —  "
            f"EV *{r['ev']*100:.1f}%*  —  Stake *{r['stake_u']:.2f}u*"
        )
        blocks += [{"type": "section", "text": {"type": "mrkdwn", "text": advice}},
                   {"type": "divider"}]

    post_slack(f"{len(hits)} edges ≥ {threshold_ev*100:.0f}% EV", blocks)
    return len(hits)
