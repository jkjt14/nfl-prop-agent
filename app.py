# app.py
import os
import pandas as pd
import streamlit as st

from agent_core import scan_edges
from config import load_config
from cleaning import clean_projections  # NEW

CFG = load_config()

st.set_page_config(page_title="NFL Prop Agent", layout="wide")

st.sidebar.header("Settings")
api_key = st.sidebar.text_input("Odds API Key", value=st.secrets.get("ODDS_API_KEY", ""), type="password")
days = st.sidebar.slider("Days ahead (events)", 1, 14, 7, 1)
profile = st.sidebar.selectbox("Market profile", list(CFG.get("markets", {}).keys()), index=0)
max_calls = st.sidebar.number_input("Max estimated credits per run", min_value=50, value=2000, step=50)
run_btn = st.sidebar.button("Run scan", type="primary")

st.title("NFL Prop Agent")
st.caption("Upload projections (raw stats CSV), clean to QB/RB/WR/TE, compute EV, and size stakes.")

uploaded = st.file_uploader("Upload raw stats CSV", type=["csv"])

if uploaded is not None:
    raw = pd.read_csv(uploaded)
    df = clean_projections(raw)

    st.success(f"Loaded & cleaned: {len(df):,} rows, {len(df.columns)} cols")
    st.dataframe(df.head(40), use_container_width=True)

    if run_btn:
        if not api_key:
            st.error("Please provide an Odds API key in the sidebar.")
        else:
            edges = scan_edges(
                df,
                CFG,
                api_key=api_key,
                days_from=days,
                profile=profile,
                max_calls=int(max_calls),
            )
            if edges is None or edges.empty:
                st.warning("No edges found (or no matching props at selected books yet).")
            else:
                st.subheader("Top Edges")
                st.dataframe(edges.head(200), use_container_width=True)

                csv_bytes = edges.to_csv(index=False).encode("utf-8")
                st.download_button("Download edges CSV", data=csv_bytes, file_name="edges_bestbook.csv", mime="text/csv")

                try:
                    with open("odds_api_calls.csv", "rb") as f:
                        st.download_button("Download API call log (CSV)", data=f, file_name="odds_api_calls.csv", mime="text/csv")
                except FileNotFoundError:
                    st.info("Run produced no usage log file (not unusual early in the week).")
else:
    st.info("Upload your projections CSV to begin.")
