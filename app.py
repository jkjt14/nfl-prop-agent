import os, io, pandas as pd, streamlit as st
from agent_core import scan_edges

st.set_page_config(page_title="NFL Prop Agent", layout="wide")

st.sidebar.header("Settings")
api_key = st.sidebar.text_input("Odds API Key", value=st.secrets.get("ODDS_API_KEY", ""), type="password")
days = st.sidebar.slider("Days ahead (events)", 1, 14, 7, 1)
profile = st.sidebar.selectbox("Market profile", ["base","heavy"], index=0)
max_calls = st.sidebar.number_input("Max estimated credits per run", min_value=50, value=2000, step=50)
run_btn = st.sidebar.button("Run scan", type="primary")

st.title("NFL Prop Agent")
st.caption("Upload projections, fetch best-book props, compute EV, and size stakes.")

uploaded = st.file_uploader("Upload projections CSV", type=["csv"])

# default config (matches CLI)
CFG = {
    "regions": "us",
    "target_books": ["fanduel","espnbet","betmgm","caesars","fanatics","ballybet"],
    "blend_alpha": 0.35,
    "markets": {
        "base": [
            "player_pass_yds","player_rush_yds","player_reception_yds","player_receptions",
            "player_pass_tds","player_rush_tds","player_reception_tds"
        ],
        "heavy": [
            "player_pass_yds","player_rush_yds","player_reception_yds","player_receptions",
            "player_pass_tds","player_rush_tds","player_reception_tds",
            "player_pass_attempts","player_pass_completions","player_interceptions",
            "player_longest_reception","player_longest_rush"
        ]
    },
    "sigma_defaults": {
        "QB": {"player_pass_yds": 60, "player_pass_tds": 0.75, "player_interceptions": 0.5,
               "player_pass_attempts": 6, "player_pass_completions": 5},
        "RB": {"player_rush_yds": 20, "player_rush_tds": 0.5, "player_receptions": 1.6,
               "player_reception_yds": 18, "player_longest_rush": 7},
        "WR": {"player_receptions": 2.0, "player_reception_yds": 30, "player_reception_tds": 0.5,
               "player_longest_reception": 8},
        "TE": {"player_receptions": 1.6, "player_reception_yds": 22, "player_reception_tds": 0.45,
               "player_longest_reception": 7}
    },
    "bankroll": 1000.0,
    "unit_pct": 0.01,
    "stake_bands": [
        {"min_ev": 0.08, "stake_u": 1.0},
        {"min_ev": 0.04, "stake_u": 0.5},
        {"min_ev": 0.02, "stake_u": 0.3}
    ]
}

if uploaded is not None:
    df = pd.read_csv(uploaded)
    st.success(f"Loaded projections: {len(df):,} rows, {len(df.columns)} cols")
    st.dataframe(df.head(20), use_container_width=True)

    if run_btn:
        if not api_key:
            st.error("Please provide an Odds API key in the sidebar.")
        else:
            edges = scan_edges(
                df, CFG, api_key=api_key,
                days_from=days, profile=profile, max_calls=int(max_calls)
            )
            if edges.empty:
                st.warning("No edges found (or no matching props at selected books).")
            else:
                st.subheader("Top Edges")
                st.dataframe(edges.head(200), use_container_width=True)

                # download results
                csv_bytes = edges.to_csv(index=False).encode("utf-8")
                st.download_button("Download edges CSV", data=csv_bytes, file_name="edges_bestbook.csv", mime="text/csv")

                # usage log (if present)
                try:
                    with open("odds_api_calls.csv","rb") as f:
                        st.download_button("Download API call log (CSV)", data=f, file_name="odds_api_calls.csv", mime="text/csv")
                except FileNotFoundError:
                    st.info("Run produced no usage log file (unexpected).")

else:
    st.info("Upload your projections CSV to begin.")
