#!/usr/bin/env Rscript
# Downloads FFA "Raw Stats" CSV as an artifact (no The Odds API calls)

suppressPackageStartupMessages({
  library(httr2)
  library(rvest)
  library(readr)
  library(dplyr)
  library(stringr)
})

# --- Configuration from env (with sensible defaults) ---
BASE   <- Sys.getenv("FFA_BASE_URL",  unset = "https://apps.fantasyfootballanalytics.net")
LOGIN  <- Sys.getenv("FFA_LOGIN_PATH",unset = "/log-in")
RAW    <- Sys.getenv("FFA_RAW_STATS_PATH", unset = "/export/raw-stats.csv")  # <-- adjust if needed
EMAIL  <- Sys.getenv("FFA_EMAIL",     unset = "")
PASS   <- Sys.getenv("FFA_PASSWORD",  unset = "")

out_dir <- "artifacts"
out_csv <- file.path(out_dir, "ffa_raw_stats.csv")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

if (EMAIL == "" || PASS == "") {
  stop("Missing FFA_EMAIL/FFA_PASSWORD secrets. Add them in GitHub → Settings → Secrets → Actions.")
}

base_url  <- str_replace(BASE, "/+$", "")
login_url <- paste0(base_url, LOGIN)
raw_url   <- paste0(base_url, RAW)

ua <- "gha-ffa-scraper/1.0 (+github-actions httr2)"

# 1) GET login page (capture cookies + CSRF if present)
resp_login_get <- request(login_url) |>
  req_user_agent(ua) |>
  req_cookie_preserve() |>
  req_perform()

# Try to extract common CSRF token field names (works on many Rails/ASP.NET sites)
html <- tryCatch(read_html(resp_body_string(resp_login_get)), error = function(e) NULL)
csrf <- NA_character_
if (!is.null(html)) {
  for (nm in c("authenticity_token","__RequestVerificationToken","csrf-token","_csrf")) {
    node <- html_element(html, paste0('input[name="', nm, '"]'))
    if (!is.na(node)) {
      csrf <- html_attr(node, "value")
      if (!is.na(csrf) && nchar(csrf) > 0) break
    }
  }
}

# 2) POST credentials (include CSRF if we found one)
form_fields <- list(
  email = EMAIL,
  password = PASS
)
if (!is.na(csrf)) {
  # include the exact name we found
  nm <- html_attr(html_element(html, 'input[name]'), "name") # fallback if site needs exact name
  # safer: try the common names in order and set the first that matches a hidden input on page
  for (cand in c("authenticity_token","__RequestVerificationToken","csrf-token","_csrf")) {
    if (!is.na(html_element(html, paste0('input[name="', cand, '"]')))) {
      form_fields[[cand]] <- csrf
      break
    }
  }
}

resp_login_post <- request(login_url) |>
  req_user_agent(ua) |>
  req_cookie_preserve() |>
  req_method("POST") |>
  req_headers(Origin = base_url, Referer = login_url) |>
  req_body_form(form_fields) |>
  req_perform()

if (resp_status(resp_login_post) >= 400) {
  stop("Login failed: HTTP ", resp_status(resp_login_post),
       ". Check credentials and login path.")
}

# 3) GET the Raw Stats CSV
resp_raw <- request(raw_url) |>
  req_user_agent(ua) |>
  req_cookie_preserve() |>
  req_headers(Referer = base_url) |>
  req_perform()

if (resp_status(resp_raw) >= 400) {
  stop("Raw Stats download failed: HTTP ", resp_status(resp_raw),
       ". Adjust FFA_RAW_STATS_PATH to the CSV export endpoint.")
}

# Save file
bin <- resp_body_raw(resp_raw)
writeBin(bin, out_csv)

# Quick sanity check: show first few lines (non-fatal if not CSV)
cat("Saved:", out_csv, " (", length(bin), "bytes )\n", sep = "")
try({
  df <- suppressWarnings(readr::read_csv(out_csv, show_col_types = FALSE, n_max = 5))
  cat("Preview columns:", paste(names(df), collapse = ", "), "\n")
})
