def scan_edges(
    projections: pd.DataFrame,
    cfg: dict,
    *,
    api_key: str,
    days_from: int = 7,
    profile: str = "base",
    max_calls: int = 1000
) -> pd.DataFrame:
    """
    projections must include ['player','team','pos'] and market means like 'player_pass_yds', etc.
    cfg keys (flexible):
      regions, target_books, sigma_defaults, blend_alpha,
      markets  <- can be a list OR a dict with keys like {base, heavy}
      bankroll, unit_pct, stake_bands
    """
    regions = cfg.get("regions", "us")

    # ---- NEW: accept list or dict for markets ----
    markets_cfg = cfg.get("markets", [])
    if isinstance(markets_cfg, dict):
        markets_list = markets_cfg.get(profile) or markets_cfg.get("base") or []
    elif isinstance(markets_cfg, (list, tuple, set)):
        markets_list = list(markets_cfg)
    else:
        markets_list = []
    # ---------------------------------------------

    target_books = set(cfg.get("target_books", []))
    sigma_defaults = cfg.get("sigma_defaults", {})
    alpha = float(cfg.get("blend_alpha", 0.35))
    bankroll = float(cfg.get("bankroll", 1000.0))
    unit_pct = float(cfg.get("unit_pct", 0.01))
    stake_bands = cfg.get("stake_bands", [
        {"min_ev": 0.08, "stake_u": 1.0},
        {"min_ev": 0.04, "stake_u": 0.5},
        {"min_ev": 0.02, "stake_u": 0.3},
    ])

    logging.info(f"[CFG] using {len(markets_list)} markets: {markets_list}")

    # Estimate credits
    events = list_upcoming_events(api_key, days_from=days_from)
    num_events = len(events or [])
    est = estimate_credits(num_events, markets_list, regions=regions)
    logging.info(f"[BUDGET] events={num_events}; markets={len(markets_list)}; estimated_credits≈{est}")

    if est > max_calls and num_events > 0 and len(markets_list) > 0:
        logging.warning(f"[BUDGET] est {est} > max_calls {max_calls}. Trimming markets.")
        keep = max(1, max_calls // num_events)
        markets_list = markets_list[:keep]
        logging.info(f"[BUDGET] trimmed markets to {len(markets_list)}")

    event_map = {e["id"]: e for e in (events or [])}

    rows = []
    for event_id, ev in event_map.items():
        if not markets_list:
            break
        markets_csv = ",".join(markets_list)
        ev_json = get_event_odds(api_key, event_id, regions=regions, odds_format="american", markets=markets_csv)

        home = (ev.get("home_team") or "").upper()
        away = (ev.get("away_team") or "").upper()

        def team_matches(t: str) -> bool:
            if not t: return False
            T = t.upper()
            return (home.find(T) != -1) or (away.find(T) != -1) or (T.find(home) != -1) or (T.find(away) != -1)

        df_ev = projections[projections["team"].apply(team_matches)] if {"team"}.issubset(projections.columns) else projections

        for _, r in df_ev.iterrows():
            player = r.get("player") or r.get("name") or ""
            if not player:
                continue
            for mkey in markets_list:
                if mkey not in r:
                    continue
                try:
                    mu = float(r[mkey])
                except Exception:
                    continue
                sd = make_variance_blend(r, mkey, sigma_defaults, alpha)
                for side in ("OVER", "UNDER"):
                    offer = best_offer_for_player(ev_json, player, mkey, side, target_books)
                    if not offer:
                        continue
                    best_book, book_line, book_odds = offer
                    p_over = prob_over(book_line, mu, sd, is_discrete=is_discrete_market(mkey))
                    win_prob = p_over if side == "OVER" else (1 - p_over)
                    ev_now = ev_per_unit(win_prob, book_odds)
                    ev_m120 = ev_per_unit(win_prob, -120)
                    ev_m110 = ev_per_unit(win_prob, -110)
                    ev_p100 = ev_per_unit(win_prob, 100)
                    playable = "YES" if ev_per_unit(win_prob, -115) > 0 else "NO"

                    unit_size = bankroll * unit_pct
                    stake_u = 0.0
                    for band in sorted(stake_bands, key=lambda x: x["min_ev"], reverse=True):
                        if ev_now >= band["min_ev"]:
                            stake_u = band["stake_u"]; break
                    stake_dollars = round(unit_size * stake_u, 2)

                    rows.append({
                        "player": player,
                        "team": r.get("team"),
                        "pos": (r.get("pos") or r.get("position")),
                        "market_key": mkey,
                        "side": side,
                        "proj_mean": round(mu, 3),
                        "model_sd": round(sd, 3),
                        "best_book": best_book,
                        "book_line": float(book_line),
                        "book_odds": int(book_odds),
                        "win_prob": round(win_prob, 4),
                        "ev_per_unit": round(ev_now, 4),
                        "playable@-115": playable,
                        "ev@-120": round(ev_m120, 4),
                        "ev@-110": round(ev_m110, 4),
                        "ev@100": round(ev_p100, 4),
                        "stake_u": stake_u,
                        "stake_$": stake_dollars,
                        "event_id": event_id,
                        "home_team": home,
                        "away_team": away
                    })

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values(["ev_per_unit", "win_prob"], ascending=[False, False], inplace=True, kind="mergesort")
        df.reset_index(drop=True, inplace=True)
    return df
