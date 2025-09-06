#!/usr/bin/env Rscript
# scripts/ffa_fetch.R
# Robust FFA "Raw Stats" fetcher.
# - Primary mode: use FFA_COOKIE to download CSV directly (recommended).
# - Fallback: try to discover/login if cookie not supplied (best-effort).
# - Has a smoke-test mode that hits a public CSV (no credentials, no API usage).
# - Extra diagnostics if FFA_DEBUG=1
# - Output path configurable via FFA_OUT or --out=...

suppressPackageStartupMessages({
  library(httr2)
  library(readr)
  library(stringr)
  library(dplyr)   # for dplyr::first
})

# ---------- small helpers ----------
`%||%` <- function(x, y) if (is.null(x) || length(x) == 0) y else x

trim_one <- function(x) {
  x <- gsub('^\\s+|\\s+$', '', x)
  x <- gsub('^"+|"+$', '', x)
  x <- gsub("^'+|'+$", "", x)
  x
}

normalize_base <- function(x) {
  x <- trim_one(x)
  if (x == "") return("")
  if (!grepl("^https?://", x, ignore.case = TRUE)) x <- paste0("https://", x)
  sub("/+$", "", x)
}

normalize_path <- function(x) {
  x <- trim_one(x)
  if (x == "") return("")
  if (grepl("^https?://", x, ignore.case = TRUE)) return(x)
  paste0("/", sub("^/*", "", x))
}

compose_url <- function(base, path_or_full) {
  p <- normalize_path(path_or_full)
  if (grepl("^https?://", p, ignore.case = TRUE)) return(p)
  b <- normalize_base(base)
  if (b == "" || p == "") stop("compose_url(): base or path empty after normalization.")
  paste0(b, p)
}

is_html_like <- function(resp) {
  ct <- tolower(resp_headers(resp)[["content-type"]] %||% "")
  if (grepl("text/html", ct)) return(TRUE)
  # if server forgot header, peek at start of body
  body_str <- tryCatch(resp_body_string(resp), error = function(e) "")
  grepl("<!doctype html|<html", tolower(substr(body_str, 1, 200)))
}

ua <- "gha-ffa-scraper/1.4 (+github-actions httr2)"

cookie_path <- Sys.getenv("FFA_COOKIE_JAR", unset = file.path(tempdir(), "ffa_cookies.jar"))
dir.create(dirname(cookie_path), recursive = TRUE, showWarnings = FALSE)

req_with <- function(url) {
  request(url) |>
    req_user_agent(ua) |>
    req_cookie_preserve(path = cookie_path)
}

# ---------- CLI args ----------
args <- commandArgs(trailingOnly = TRUE)
arg_has <- function(flag) any(grepl(paste0("^", flag, "($|=)"), args))
arg_get <- function(key, default=NULL) {
  hit <- grep(paste0("^", key, "="), args, value = TRUE)
  if (length(hit)) sub(paste0("^", key, "="), "", hit[[1]]) else default
}

SMOKE <- isTRUE(as.logical(arg_get("--smoke-test", "FALSE")))
OUT_CLI <- arg_get("--out", NULL)
RAW_CLI <- arg_get("--raw-url", NULL)

# ---------- config (env + CLI) ----------
BASE_RAW   <- Sys.getenv("FFA_BASE_URL",  unset = "https://apps.fantasyfootballanalytics.net")
EMAIL      <- trim_one(Sys.getenv("FFA_EMAIL", unset = ""))
PASS       <- trim_one(Sys.getenv("FFA_PASSWORD", unset = ""))
COOKIE_RAW <- trim_one(Sys.getenv("FFA_COOKIE", unset = ""))
DEBUG      <- identical(trim_one(Sys.getenv("FFA_DEBUG", unset = "")), "1")

# Accept either name for convenience
RAW_FULL_ENV <- trim_one(Sys.getenv("FFA_RAW_STATS_URL", unset = Sys.getenv("FFA_RAW_URL", unset = "")))

# Optional smoke URL (or default to a public CSV)
SMOKE_URL <- Sys.getenv("FFA_SMOKE_URL",
  unset = "https://raw.githubusercontent.com/cs109/2014_data/master/countries.csv"
)

# Allow explicit full URLs/paths for login (fallback mode)
LOGIN_FULL <- trim_one(Sys.getenv("FFA_LOGIN_URL", unset = ""))
RAW_FULL   <- if (!is.null(RAW_CLI) && RAW_CLI != "") trim_one(RAW_CLI) else RAW_FULL_ENV

LOGIN_PATH <- Sys.getenv("FFA_LOGIN_PATH", unset = "")
RAW_PATH   <- Sys.getenv("FFA_RAW_STATS_PATH", unset = "/export/raw-stats.csv")

# Candidate login paths if we must auto-discover
CANDIDATE_LOGIN <- c(
  "/log-in", "/login", "/signin", "/sign-in",
  "/users/sign_in", "/account/login", "/Account/Login",
  "/auth/login", "/wp-login.php", "/accounts/login/", "/member/login"
)

base_url <- normalize_base(BASE_RAW)

raw_url <- if (SMOKE) {
  SMOKE_URL
} else if (RAW_FULL != "") {
  normalize_path(RAW_FULL) # already full
} else {
  compose_url(base_url, if (RAW_PATH != "") RAW_PATH else "/export/raw-stats.csv")
}

login_url <- NA_character_
if (COOKIE_RAW != "") {
  login_mode <- "cookie"
} else if (LOGIN_FULL != "") {
  login_url  <- normalize_path(LOGIN_FULL)
  login_mode <- "explicit"
} else if (LOGIN_PATH != "") {
  login_url  <- compose_url(base_url, LOGIN_PATH)
  login_mode <- "path"
} else {
  login_mode <- "auto"
}

out_csv <- OUT_CLI %||% Sys.getenv("FFA_OUT", unset = file.path("artifacts", if (SMOKE) "ffa_smoke.csv" else "ffa_raw_stats.csv"))

if (DEBUG) {
  cat("DEBUG config:\n")
  cat("  base_url   :", base_url, "\n")
  cat("  raw_url    :", raw_url, "\n")
  cat("  out_csv    :", out_csv, "\n")
  cat("  login_mode :", login_mode, "\n")
  if (!is.na(login_url)) cat("  login_url  :", login_url, "\n")
}

# ---------- fetchers ----------
download_raw_with_cookie <- function(cookie_header, url) {
  if (DEBUG) cat("Using cookie-only mode to GET raw CSV …\n")
  request(url) |>
    req_user_agent(ua) |>
    req_headers(Cookie = cookie_header, Referer = base_url) |>
    req_perform()
}

discover_login <- function(base_url, candidates) {
  if (DEBUG) cat("Auto-discovering login URL…\n")
  tried <- character(0)
  for (p in candidates) {
    u <- compose_url(base_url, p)
    tried <- c(tried, u)
    resp <- try(req_with(u) |> req_perform(), silent = TRUE)
    if (!inherits(resp, "try-error")) {
      status <- resp_status(resp)
      if (status < 400 && is_html_like(resp)) {
        if (DEBUG) cat("  Found candidate login:", u, " (HTTP ", status, ")\n", sep = "")
        return(u)
      }
    }
  }
  stop("Could not find a working login page.\n",
       "Tried:\n- ", paste(tried, collapse = "\n- "),
       "\nTip: set FFA_COOKIE with a browser cookie to bypass login, ",
       "or set FFA_LOGIN_URL and FFA_RAW_STATS_URL explicitly.")
}

perform_login <- function(login_url, email, pass) {
  if (email == "" || pass == "") {
    stop("Missing FFA_EMAIL and/or FFA_PASSWORD. Add them as secrets or env vars.")
  }

  resp_get <- try(req_with(login_url) |> req_perform(), silent = TRUE)
  if (inherits(resp_get, "try-error") || resp_status(resp_get) >= 400) {
    stop("Could not GET login page '", login_url, "'. HTTP ",
         if (!inherits(resp_get, "try-error")) resp_status(resp_get) else "error")
  }

  # Optional CSRF parsing (only if rvest is available)
  form <- list()
  if (requireNamespace("rvest", quietly = TRUE)) {
    html <- suppressWarnings(try(rvest::read_html(resp_body_string(resp_get)), silent = TRUE))
    csrf_name <- NULL; csrf_value <- NULL
    user_field <- NULL; pass_field <- NULL

    if (!inherits(html, "try-error")) {
      nodes <- rvest::html_elements(html, 'input[name]')
      names_all <- unique(na.omit(rvest::html_attr(nodes, "name")))
      hidden_nodes <- rvest::html_elements(html, 'input[type="hidden"][name]')
      for (n in hidden_nodes) {
        nm <- rvest::html_attr(n, "name"); val <- rvest::html_attr(n, "value")
        if (!is.na(nm) && grepl("csrf|token|verification", nm, ignore.case = TRUE)) {
          csrf_name <- nm; csrf_value <- val; break
        }
      }
      user_field <- dplyr::first(c("email","username","login")[c("email","username","login") %in% names_all])
      pass_field <- dplyr::first(c("password","pass","passwd")[c("password","pass","passwd") %in% names_all])
    }

    form[[ ifelse(is.null(user_field), "email", user_field) ]] <- email
    form[[ ifelse(is.null(pass_field), "password", pass_field) ]] <- pass
    if (!is.null(csrf_name) && !is.null(csrf_value)) form[[csrf_name]] <- csrf_value
  } else {
    # Blind post (works only on simple forms)
    form[["email"]] <- email
    form[["password"]] <- pass
  }

  resp_post <- req_with(login_url) |>
    req_method("POST") |>
    req_headers(Origin = base_url, Referer = login_url) |>
    req_body_form(form) |>
    req_perform()

  if (resp_status(resp_post) >= 400) {
    stop("Login POST failed: HTTP ", resp_status(resp_post))
  }
  invisible(TRUE)
}

save_resp_to_csv <- function(resp, out_csv) {
  dir.create(dirname(out_csv), recursive = TRUE, showWarnings = FALSE)
  if (is_html_like(resp)) {
    snippet <- tryCatch(substr(resp_body_string(resp), 1, 400), error = function(e) "<non-text body>")
    stop("Expected CSV, but got HTML (likely not authenticated). Snippet:\n", snippet)
  }
  bin <- resp_body_raw(resp)
  writeBin(bin, out_csv)
  cat("Saved:", out_csv, " (", length(bin), " bytes)\n", sep = "")
  # small preview (best-effort)
  try({
    df <- suppressWarnings(readr::read_csv(out_csv, n_max = 5, show_col_types = FALSE))
    cat("Preview columns:", paste(names(df), collapse = ", "), "\n")
  }, silent = TRUE)
}

# ---------- main ----------
if (SMOKE) {
  if (DEBUG) cat("Smoke-test mode: ", raw_url, "\n", sep = "")
  resp <- request(raw_url) |> req_user_agent(ua) |> req_perform()
  if (resp_status(resp) >= 400) stop("Smoke test download failed: HTTP ", resp_status(resp))
  save_resp_to_csv(resp, out_csv)
  quit(status = 0)
}

if (COOKIE_RAW != "") {
  resp_raw <- download_raw_with_cookie(COOKIE_RAW, raw_url)
  if (resp_status(resp_raw) >= 400) {
    stop("Raw Stats download failed with cookie: HTTP ", resp_status(resp_raw))
  }
  save_resp_to_csv(resp_raw, out_csv)
  quit(status = 0)
}

# Fallback: attempt login-based fetch
login_url <- if (!is.na(login_url)) login_url else discover_login(base_url, CANDIDATE_LOGIN)
if (DEBUG) cat("Using login URL:", login_url, "\n")

perform_login(login_url, EMAIL, PASS)

resp_raw <- req_with(raw_url) |>
  req_headers(Referer = base_url) |>
  req_perform()

if (resp_status(resp_raw) >= 400) {
  stop("Raw Stats download failed post-login: HTTP ", resp_status(resp_raw),
       ". If the site uses JS-only auth, set FFA_COOKIE to bypass login.")
}

save_resp_to_csv(resp_raw, out_csv)
