# R/fetch_projections.R
# Purpose: Fetch weekly NFL projections with ffanalytics and write the CSV the Python agent expects.

options(repos = c(CRAN = "https://cloud.r-project.org"))

suppressPackageStartupMessages({
  library(ffanalytics)   # installed in the workflow step
  library(dplyr)
  library(readr)
  library(stringr)
})

# --- 1) SCRAPE the current week (leave season/week NULL to auto-detect current) ---
# You can edit 'src' to sources you trust. The README documents weekly sources.  :contentReference[oaicite:1]{index=1}
scr <- scrape_data(
  src = c("CBS","ESPN","FantasyPros","NumberFire","NFL","FFToday","FantasySharks","FleaFlicker","FantasyFootballNerd"),
  pos = c("QB","RB","WR","TE"),
  season = NULL,  # current season
  week   = NULL   # current week
)

# --- 2) BUILD blended projections table (weighted average per FFA) ---
proj <- projections_table(scr, avg_type = "weighted")  # average|robust|weighted  :contentReference[oaicite:2]{index=2}

# proj now has standardized stat columns per position.
# We'll map them to the column names your Python agent expects.
# Note: not every position has every stat; NA is fine.

norm <- proj %>%
  mutate(
    player = str_squish(paste(first_name, last_name)),
    team   = toupper(team),
    pos    = toupper(pos)
  ) %>%
  transmute(
    player,
    team,
    pos,
    # Passing
    player_pass_yds      = suppressWarnings(as.numeric(pass_yds)),
    player_pass_tds      = suppressWarnings(as.numeric(pass_tds)),
    # Rushing
    player_rush_yds      = suppressWarnings(as.numeric(rush_yds)),
    player_rush_tds      = suppressWarnings(as.numeric(rush_tds)),
    # Receiving
    player_reception_yds = suppressWarnings(as.numeric(rec_yds)),
    player_reception_tds = suppressWarnings(as.numeric(rec_tds)),
    player_receptions    = suppressWarnings(as.numeric(receptions))
  ) %>%
  distinct()

dir.create("data", showWarnings = FALSE)
write_csv(norm, "data/projections_ffa_week.csv", na = "")
cat(sprintf("Wrote data/projections_ffa_week.csv with %d rows\n", nrow(norm)))
