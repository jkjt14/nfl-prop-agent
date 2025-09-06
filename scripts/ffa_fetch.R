#!/usr/bin/env Rscript
# Downloads FFA "Raw Stats" CSV as an artifact (no The Odds API calls)

suppressPackageStartupMessages({
  library(httr2)
  library(rvest)
  library(readr)
  library(dplyr)
  library(stringr)
})

# --- Config from env (with defaults) ---
BASE   <- Sys.getenv("FFA_BASE_URL",  unset = "https://apps.fantasyfootballanalytics.net")
LOGIN  <- Sys.getenv("FFA_LOGIN_PATH",unset = "/log-in")
RAW    <- Sys.getenv("FFA_RAW_STATS_PATH", unset = "/export/raw-stats.csv")  # adjust via secret if needed
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

ua <- "gha-ffa-scraper/1.1 (+github-actions httr2)"

# -------- Cookie jar (FIX for your error) --------
cookie_path <- Sys.getenv("FFA_COOKIE_JAR", unset = file.path(tempdir(), "ffa_cookies.jar"))
dir.create(dirname(cookie_path), recursive = TRUE, showWarnings = FALSE)

req_with <- function(url) {
  request(url) |>
    req_user_agent(ua) |>
    req_cookie_preserve(path = cookie_path)
}

# 1) GET login page (capture cookies + CSRF if present)
resp_login_get <- req_with(login_url) |> req_perform()

# Try to extract CSRF + infer field names
html <- suppressWarnings(try(read_html(resp_body_string(resp_login_get)), silent = TRUE))
inputs <- character()
csrf_name <- NULL; csrf_value <- NULL
user_field <- NULL; pass_field <- NULL

if (!inherits(html, "try-error")) {
  nodes <- html_elements(html, 'input[name]')
  inputs <- unique(na.omit(html_attr(nodes, "name")))

  # csrf / token field
  hidden_nodes <- html_elements(html, 'input[type="hidden"][name]')
  for (n in hidden_nodes) {
    nm <- html_attr(n, "name"); val <- html_attr(n, "value")
    if (!is.na(nm) && grepl("csrf|token|verification", nm, ignore.case = TRUE)) {
      csrf_name <- nm; csrf_value <- val; break
    }
  }

  # common username/password field names
  user_field <- dplyr::first(c("email","username","login")[c("email","username","login") %in% inputs])
  pass_field <- dplyr::first(c("password","pass","passwd")[c("password","pass","passwd") %in% inputs])
}

# 2) POST credentials
form_fields <- list()
if (!is.null(user_field)) form_fields[[user_field]] <- EMAIL else form_fields[["email"]] <- EMAIL
if (!is.null(pass_field)) form_fields[[pass_field]] <- PASS  else form_fields[["password"]] <- PASS
if (!is.null(csrf_name) && !is.null(csrf_value)) form_fields[[csrf_name]] <- csrf_value

resp_login_post <- req_with(login_url) |>
  req_method("POST") |>
  req_headers(Origin = base_url, Referer = login_url) |>
  req_body_form(form_fields) |>
  req_perform()

if (resp_status(resp_login_post) >= 400) {
  stop("Login failed: HTTP ", resp_status(resp_login_post),
       ". Check credentials and LOGIN path. (Cookie jar: ", cookie_path, ")")
}

# 3) GET the Raw Stats CSV
resp_raw <- req_with(raw_url) |>
  req_headers(Referer = base_url) |>
  req_perform()

if (resp_status(resp_raw) >= 400) {
  stop("Raw Stats download failed: HTTP ", resp_status(resp_raw),
       ". Adjust FFA_RAW_STATS_PATH to the CSV export endpoint.")
}

# Save file
bin <- resp_body_raw(resp_raw)
writeBin(bin, out_csv)

cat("Saved:", out_csv, " (", length(bin), " bytes)\n", sep = "")
# Quick preview if it is a CSV
try({
  df <- suppressWarnings(readr::read_csv(out_csv, show_col_types = FALSE, n_max = 5))
  cat("Preview columns:", paste(names(df), collapse = ", "), "\n")
})
