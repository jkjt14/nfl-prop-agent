#!/usr/bin/env Rscript
# Downloads FFA "Raw Stats" CSV as an artifact (no The Odds API calls)
# Hardened URL handling + verbose debug to avoid malformed-URL errors.

suppressPackageStartupMessages({
  library(httr2)
  library(rvest)
  library(readr)
  library(dplyr)
  library(stringr)
})

# ---------- Helpers ----------
trim_one <- function(x) {
  # remove surrounding whitespace and any stray quotes
  x <- gsub('^\\s+|\\s+$', '', x)
  x <- gsub('^"+|"+$', '', x)
  x <- gsub("^'+|'+$", "", x)
  x
}

normalize_base <- function(x) {
  x <- trim_one(x)
  if (x == "") return("")
  # allow both host and full URL
  if (!grepl("^https?://", x, ignore.case = TRUE)) x <- paste0("https://", x)
  # remove trailing slashes only (not the scheme)
  x <- sub("/+$", "", x)
  x
}

normalize_path <- function(x) {
  x <- trim_one(x)
  if (x == "") return("")
  if (grepl("^https?://", x, ignore.case = TRUE)) {
    # caller passed a full URL; return as-is (but trim)
    return(x)
  }
  # ensure single leading slash
  x <- paste0("/", sub("^/*", "", x))
  x
}

compose_url <- function(base, path_or_full) {
  p <- normalize_path(path_or_full)
  if (grepl("^https?://", p, ignore.case = TRUE)) return(p)
  b <- normalize_base(base)
  if (b == "" || p == "") stop("compose_url(): base or path is empty after normalization.")
  paste0(b, p)
}

# ---------- Config from env ----------
BASE_RAW <- Sys.getenv("FFA_BASE_URL",  unset = "https://apps.fantasyfootballanalytics.net")
LOGIN_RAW <- Sys.getenv("FFA_LOGIN_PATH", unset = "/log-in")
RAW_STATS_RAW <- Sys.getenv("FFA_RAW_STATS_PATH", unset = "/export/raw-stats.csv")
EMAIL <- trim_one(Sys.getenv("FFA_EMAIL", unset = ""))
PASS  <- trim_one(Sys.getenv("FFA_PASSWORD", unset = ""))
DEBUG <- identical(trim_one(Sys.getenv("FFA_DEBUG", unset = "")), "1")

if (EMAIL == "" || PASS == "") {
  stop("Missing FFA_EMAIL/FFA_PASSWORD secrets. Add them in GitHub → Settings → Secrets → Actions.")
}

# Build URLs robustly (accept host, base url, or full URLs in the PATH secrets)
login_url <- tryCatch({
  compose_url(BASE_RAW, LOGIN_RAW)
}, error = function(e) {
  stop("Failed to construct LOGIN URL. base='", BASE_RAW, "' path='", LOGIN_RAW, "'\n", conditionMessage(e))
})

raw_url <- tryCatch({
  compose_url(BASE_RAW, RAW_STATS_RAW)
}, error = function(e) {
  stop("Failed to construct RAW-STATS URL. base='", BASE_RAW, "' path='", RAW_STATS_RAW, "'\n", conditionMessage(e))
})

if (DEBUG) {
  cat("DEBUG URLs:\n")
  cat("  login_url: ", login_url, "\n", sep = "")
  cat("  raw_url  : ", raw_url, "\n", sep = "")
}

ua <- "gha-ffa-scraper/1.2 (+github-actions httr2)"

# -------- Cookie jar (persist across reqs) --------
cookie_path <- Sys.getenv("FFA_COOKIE_JAR", unset = file.path(tempdir(), "ffa_cookies.jar"))
dir.create(dirname(cookie_path), recursive = TRUE, showWarnings = FALSE)

req_with <- function(url) {
  request(url) |>
    req_user_agent(ua) |>
    req_cookie_preserve(path = cookie_path)
}

# ---------- 1) GET login page ----------
resp_login_get <- tryCatch({
  req_with(login_url) |> req_perform()
}, error = function(e) {
  msg <- paste0("Could not GET login page '", login_url, "'.\n", conditionMessage(e))
  stop(msg)
})

# ---------- 2) Extract form fields (CSRF, etc.) ----------
html <- suppressWarnings(try(read_html(resp_body_string(resp_login_get)), silent = TRUE))
inputs <- character()
csrf_name <- NULL; csrf_value <- NULL
user_field <- NULL; pass_field <- NULL

if (!inherits(html, "try-error")) {
  nodes <- html_elements(html, 'input[name]')
  inputs <- unique(na.omit(html_attr(nodes, "name")))

  hidden_nodes <- html_elements(html, 'input[type="hidden"][name]')
  for (n in hidden_nodes) {
    nm <- html_attr(n, "name"); val <- html_attr(n, "value")
    if (!is.na(nm) && grepl("csrf|token|verification", nm, ignore.case = TRUE)) {
      csrf_name <- nm; csrf_value <- val; break
    }
  }

  user_field <- dplyr::first(c("email","username","login")[c("email","username","login") %in% inputs])
  pass_field <- dplyr::first(c("password","pass","passwd")[c("password","pass","passwd") %in% inputs])
}

# ---------- 3) POST credentials ----------
form_fields <- list()
if (!is.null(user_field)) form_fields[[user_field]] <- EMAIL else form_fields[["email"]] <- EMAIL
if (!is.null(pass_field)) form_fields[[pass_field]] <- PASS  else form_fields[["password"]] <- PASS
if (!is.null(csrf_name) && !is.null(csrf_value)) form_fields[[csrf_name]] <- csrf_value

resp_login_post <- tryCatch({
  req_with(login_url) |>
    req_method("POST") |>
    req_headers(Origin = normalize_base(BASE_RAW), Referer = login_url) |>
    req_body_form(form_fields) |>
    req_perform()
}, error = function(e) {
  stop("Login POST failed.\n", conditionMessage(e))
})

if (resp_status(resp_login_post) >= 400) {
  stop("Login failed: HTTP ", resp_status(resp_login_post),
       ". Check credentials and LOGIN path. Cookie jar: ", cookie_path)
}

# ---------- 4) GET the Raw Stats CSV ----------
resp_raw <- tryCatch({
  req_with(raw_url) |>
    req_headers(Referer = normalize_base(BASE_RAW)) |>
    req_perform()
}, error = function(e) {
  stop("Raw Stats GET failed.\n", conditionMessage(e))
})

if (resp_status(resp_raw) >= 400) {
  stop("Raw Stats download failed: HTTP ", resp_status(resp_raw),
       ". Adjust FFA_RAW_STATS_PATH to the CSV export endpoint.")
}

# ---------- 5) Save file & preview ----------
out_dir <- "artifacts"
out_csv <- file.path(out_dir, "ffa_raw_stats.csv")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

bin <- resp_body_raw(resp_raw)
writeBin(bin, out_csv)
cat("Saved:", out_csv, " (", length(bin), " bytes)\n", sep = "")

# Try to preview columns (if CSV)
try({
  df <- suppressWarnings(readr::read_csv(out_csv, show_col_types = FALSE, n_max = 5))
  cat("Preview columns:", paste(names(df), collapse = ", "), "\n")
})
