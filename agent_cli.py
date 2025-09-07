# agent_cli.py
import os, sys
import pandas as pd

from agent_core import scan_edges
from alerts import alert_edges

def load_projections(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        alt = "data/raw_stats_current.csv"
        if os.path.exists(alt):
            path = alt
    if not os.path.exists(path):
        raise FileNotFoundError(f"Projections CSV not found at {path}.")
    df = pd.read_csv(path)
    # Normalize core columns
    for c in ("player", "team", "pos"):
        if c not in df.columns:
            for altc in [c.title(), c.upper(), "Position" if c=="pos" else c]:
                if altc in df.columns:
                    df.rename(columns={altc: c}, inplace=True)
                    break
    print(f"Loaded projections: {len(df):,} rows, {len(df.columns)} cols from {path}")
    return df

def default_config():
    target_books = [
        "draftkings","fanduel","caesars","betmgm","pointsbetus","bet365",
        "barstool","espnbet","betway"
    ]
    markets = {
        "base": [
            "player_pass_yds","player_rush_yds","player_reception_yds",
            "player_receptions","player_pass_tds","player_rush_tds",
            "player_reception_tds","player_interceptions",
            "player_pass_completions","player_longest_reception","player_longest_rush"
        ],
        "heavy": [
            "player_pass_yds","player_rush_yds","player_reception_yds",
            "player_receptions","player_pass_tds","player_rush_tds",
            "player_reception_tds","player_interceptions",
            "player_pass_completions","player_pass_attempts",
            "player_longest_reception","player_longest_rush"
        ]
    }
    sigma_defaults = {
        "QB": {
            "player_pass_yds": 45.0, "player_pass_tds": 0.7, "player_interceptions": 0.5,
            "player_pass_completions": 5.5, "player_pass_attempts": 6.5
        },
        "RB": {
            "player_rush_yds": 26.0, "player_rush_tds": 0.5, "player_receptions": 1.1,
            "player_reception_yds": 18.0, "player_longest_rush": 9.0
        },
        "WR": {
            "player_receptions": 1.2, "player_reception_yds": 22.0, "player_reception_tds": 0.5,
            "player_longest_reception": 10.0
        },
        "TE": {
            "player_receptions": 1.0, "player_reception_yds": 18.0, "player_reception_tds": 0.45,
            "player_longest_reception": 8.0
        }
    }
    return {
        "regions": "us,us2",
        "target_books": target_books,
        "markets": markets,
        "sigma_defaults": sigma_defaults,
        "blend_alpha": 0.35,
        "bankroll": 2000.0,
        "unit_pct": 0.01,
        "stake_bands": [
            {"min_ev": 0.08, "stake_u": 1.0},
            {"min_ev": 0.05, "stake_u": 0.7},
            {"min_ev": 0.03, "stake_u": 0.5},
        ]
    }

def advice_lines(df: pd.DataFrame, threshold: float) -> str:
    if df is None or df.empty:
        return "No edges found."
    name_map = {
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
    keep = df[df["ev_per_unit"] >= threshold].copy()
    if keep.empty:
        return "No edges ≥ threshold."
    lines = []
    for _, r in keep.head(50).iterrows():
        evp = f"{r['ev_per_unit']*100:.1f}%"
        lines.append(
            f"{r['player']} {r['side']} {r['book_line']} {name_map.get(r['market_key'], r['market_key'])} — "
            f"{r['book_odds']} ({r['best_book']}) | EV {evp} | {r['stake_u']}u"
        )
    return "\n".join(lines)

def main() -> int:
    proj_path = os.environ.get("PROJECTIONS_PATH", "data/projections.csv")
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("Missing ODDS_API_KEY.")
        return 2

    threshold = float(os.environ.get("EDGE_THRESHOLD", "0.06"))
    profile = os.environ.get("MARKETS_PROFILE", "base")
    df_proj = load_projections(proj_path)

    cfg = default_config()
    df_edges = scan_edges(
        df_proj,
        cfg,
        api_key=api_key,
        days_from=7,
        profile=profile,
        max_calls=1000
    )

    os.makedirs("artifacts", exist_ok=True)
    if df_edges is not None and not df_edges.empty:
        df_edges.to_csv("artifacts/edges.csv", index=False)

    adv = advice_lines(df_edges, threshold)
    with open("artifacts/advice.txt", "w", encoding="utf-8") as f:
        f.write(adv + "\n")

    print("\n=== ADVICE ===\n" + adv + "\n")

    alert_edges(df_edges, threshold_ev=threshold)
    return 0

if __name__ == "__main__":
    sys.exit(main())