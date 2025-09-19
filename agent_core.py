def scan_edges(events, projections, config, odds_client, week):
    """
    Scan events for betting edges.
    - events: list of event dicts from The Odds API
    - projections: DataFrame of player projections
    - config: dict loaded from agent_config.yaml
    - odds_client: Odds API client
    - week: week number
    """
    import os
    import logging
    import pandas as pd

    edges = []
    reasons = {}

    for ev in events:
        ev_id = ev.get("id")
        home, away = ev.get("home_team"), ev.get("away_team")
        try:
            ev_json = odds_client.get_event_odds(ev_id, config["markets_api"])
        except Exception as e:
            logging.error("Failed odds fetch for %s: %s", ev_id, e)
            continue

        # Debug: log the markets each bookmaker actually returned (once only)
        if os.environ.get("LOG_MARKETS_ONCE", "1") == "1":
            seen = {}
            for bm in ev_json.get("bookmakers", []) or []:
                bk = bm.get("key")
                keys = sorted({mk.get("key") for mk in (bm.get("markets") or []) if mk.get("key")})
                seen[bk] = keys
            logging.info("[DEBUG] markets_by_book: %s", seen)
            os.environ["LOG_MARKETS_ONCE"] = "0"

        # Filter to target books if specified
        valid_books = config.get("target_books", [])
        bookmakers = ev_json.get("bookmakers", [])
        if valid_books:
            bookmakers = [bm for bm in bookmakers if bm["key"] in valid_books]

        if not bookmakers:
            reasons.setdefault("no_bookmakers_for_event", 0)
            reasons["no_bookmakers_for_event"] += 1
            continue

        # Filter projections to teams in this event
        team_mask = (projections["team"].isin([home, away]))
        team_proj = projections[team_mask]
        if team_proj.empty:
            reasons.setdefault("team_filter_empty", 0)
            reasons["team_filter_empty"] += 1
            continue

        # Try each player in projections
        for _, prow in team_proj.iterrows():
            for market in config["markets"]:
                col = resolve_market_column(market)
                val = prow.get(col)
                if pd.isna(val):
                    reasons.setdefault(f"missing_projection_value::{market}", 0)
                    reasons[f"missing_projection_value::{market}"] += 1
                    continue

                best_offer = best_offer_for_player(
                    prow["player"], market, val, bookmakers, config
                )
                if best_offer:
                    edges.append(best_offer)
                else:
                    reasons.setdefault(f"no_offer::{market}", 0)
                    reasons[f"no_offer::{market}"] += 1

    logging.info("Scan reasons summary: %s", reasons)
    return edges
