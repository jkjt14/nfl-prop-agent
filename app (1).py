
import os, io, pandas as pd, streamlit as st
from datetime import datetime
import yaml, requests
from agent_cli import load_config, infer_discrete
from agent_core import (list_upcoming_events, get_event_odds, best_offer_for_player,
                        make_variance_blend, prob_over, ev_per_unit, stake_units)

st.set_page_config(page_title="NFL Prop Agent", layout="wide")
st.title("🏈 NFL Prop Agent (MA Books)")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Odds API Key", value=os.environ.get("ODDS_API_KEY",""), type="password")
    max_days = st.slider("Days ahead (events)", 1, 10, 7)
    run_button = st.button("Run scan")

cfg = load_config("agent_config.yaml")
if api_key:
    os.environ["ODDS_API_KEY"] = api_key

uploaded = st.file_uploader("Upload projections CSV", type=["csv"])
if uploaded is not None:
    proj = pd.read_csv(uploaded)
    st.success(f"Loaded projections: {proj.shape[0]} rows, {proj.shape[1]} cols")
    st.dataframe(proj.head(10))

    if run_button:
        with st.spinner("Pulling events and odds..."):
            events = list_upcoming_events(os.environ.get("ODDS_API_KEY",""), cfg.get("sport_key","americanfootball_nfl"), days_from=max_days)
            rows = []
            for ev in events:
                ev_odds = get_event_odds(os.environ["ODDS_API_KEY"], ev["id"], cfg["regions"], cfg["odds_format"], cfg["markets"])
                for _, r in proj.iterrows():
                    name = r.get("player"); team = r.get("team"); pos = r.get("position")
                    for mkey in cfg["markets"]:
                        simple = mkey.replace("player_","")
                        mu = None
                        if mkey in proj.columns and not pd.isna(r[mkey]): mu = float(r[mkey])
                        elif simple in proj.columns and not pd.isna(r[simple]): mu = float(r[simple])
                        if mu is None: continue
                        sd = make_variance_blend(r, mkey, cfg["outcome_sigma"], float(cfg.get("blend_alpha",0.35)))
                        for side in ["OVER","UNDER"]:
                            best = best_offer_for_player(ev_odds, name, mkey, side, set(cfg["target_books"]))
                            if not best: continue
                            book, line, price = best
                            is_disc = infer_discrete(mkey)
                            p = (1 - 0)  # placeholder; compute win prob
                            if side=="OVER":
                                p = 1 - (0.5*(1 + ((line + (0.5 if is_disc else 0.0)) - mu)/sd)) if sd>0 else (1.0 if mu>line else 0.0)
                            # Better: reuse core prob; but we keep it consistent:
                            from agent_core import prob_over
                            p = prob_over(line, mu, sd, is_discrete=is_disc) if side=="OVER" else 1 - prob_over(line, mu, sd, is_discrete=is_disc)
                            ev_act = ev_per_unit(p, price)
                            row = {
                                "player": name, "team": team, "pos": pos,
                                "market_key": mkey, "side": side,
                                "proj_mean": round(mu,2), "model_sd": round(sd,2),
                                "best_book": book, "book_line": line, "book_odds": price,
                                "win_prob": round(p,4), "ev_per_unit": round(ev_act,4),
                                "playable@-115": "YES" if (price >= cfg["max_juice"] if price<0 else True) else "NO"
                            }
                            for lvl in cfg["odds_levels"]:
                                from agent_core import ev_per_unit as evu
                                row[f"ev@{lvl}"] = round(evu(p, int(lvl)),4)
                            rows.append(row)
            if not rows:
                st.warning("No matches found—try expanding markets or check game window.")
            else:
                out = pd.DataFrame(rows)
                unit_value = float(cfg["bankroll"]) * float(cfg["unit_pct"])
                stakes_u = out["ev_per_unit"].apply(lambda ev: stake_units(ev, cfg["ev_bands"]))
                out["stake_u"] = stakes_u
                out["stake_$"] = (out["stake_u"] * unit_value).round(2)
                playable = out[out["playable@-115"]=="YES"]
                top = (playable if len(playable)>=cfg["top_n"] else out).sort_values("ev_per_unit", ascending=False).head(cfg["top_n"])
                st.subheader("Top Edges")
                st.dataframe(top)
                csv = top.to_csv(index=False).encode("utf-8")
                st.download_button("Download CSV", data=csv, file_name="edges_bestbook.csv", mime="text/csv")
else:
    st.info("Enter your Odds API key in the sidebar to enable fetching lines.")
