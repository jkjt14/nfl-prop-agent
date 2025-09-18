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

use_repo_latest = st.checkbox("Use latest file from repo (data/raw_stats_YYYY_wkN.csv) if nothing uploaded", value=False)

uploaded = st.file_uploader("Upload raw stats CSV", type=["csv"])

if uploaded is not None:
    raw = pd.read_csv(uploaded)
    df = clean_projections(raw)
    st.success(f"Loaded & cleaned (uploaded): {len(df):,} rows, {len(df.columns)} cols")
elif use_repo_latest:
    from file_finder import resolve_projection_path
    proj_path, year, week = resolve_projection_path(None)
    raw = pd.read_csv(proj_path)
    df = clean_projections(raw)
    wk_txt = f" {year} wk{week}" if year and week else ""
    st.success(f"Loaded & cleaned (repo latest{wk_txt}): {len(df):,} rows, {len(df.columns)} cols")
else:
    st.info("Upload your projections CSV to begin.")
    st.stop()

st.subheader("Projection preview")
st.dataframe(df.head(25))

if not run_btn:
    st.info("Adjust settings in the sidebar and click **Run scan** to query sportsbook odds.")
    st.stop()

if not api_key:
    st.error("Provide an Odds API key to run the scan.")
    st.stop()

with st.spinner("Scanning for edges..."):
    try:
        edges = scan_edges(
            df,
            CFG,
            api_key=api_key,
            days_from=int(days),
            profile=str(profile),
            max_calls=int(max_calls),
        )
    except Exception as exc:  # Streamlit surfaces the stack trace when requested
        st.error(f"Scan failed: {exc}")
        st.stop()

if edges is None or edges.empty:
    st.warning("Scan completed but no qualifying edges were found.")
else:
    st.success(f"Found {len(edges)} edges. Showing top {min(len(edges), 50)}")
    st.dataframe(edges.head(50))
    st.download_button(
        "Download edges as CSV",
        data=edges.to_csv(index=False).encode("utf-8"),
        file_name="edges.csv",
        mime="text/csv",
    )
