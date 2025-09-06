#!/usr/bin/env Rscript
# Robust FFA "Raw Stats" fetcher.
# - Tries multiple common login URLs automatically
# - OR, if FFA_COOKIE is provided, skips login and just downloads the CSV
# - Prints debug info if FFA_DEBUG=1

suppressPackageStartupMessages({
  library(httr2)
  library(rvest)
  library(readr)
  library(dplyr)
  library(stringr)
})

# ----------------- helpers -----------------
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

ua <- "gha-ffa-scraper/1.3 (+github-actions httr2)"

cookie_path <- Sys.getenv("FFA_COOKIE_JAR", unset = file.path(tempdir(), "ffa_cookies.jar"))
dir.create(dirname(cookie_path), recursive = TRUE, showWarnings = FALSE)

req_with <- function(url) {
  request(url) |>
    req_user_agent(ua) |>
    req_cookie_preserve(path = cookie_path)
}

# ----------------- config -----------------
BASE_RAW   <- Sys.getenv("FFA_BASE_URL",  unset = "https://apps.fantasyfootballanalytics.net")
EMAIL      <- trim_one(Sys.getenv("FFA_EMAIL", unset = ""))
PASS       <- trim_one(Sys.getenv("FFA_PASSWORD", unset = ""))
COOKIE_RAW <- trim_one(Sys.getenv("FFA_COOKIE", unset = ""))
DEBUG      <- identical(trim_one(Sys.getenv("FFA_DEBUG", unset = "")), "1")

# Allow explicit full URLs (take precedence if provided)
LOGIN_FULL <- trim_one(Sys.getenv("FFA_LOGIN_URL", unset = ""))
RAW_FULL   <- trim_one(Sys.getenv("FFA_RAW_STATS_URL", unset = ""))

# Allow paths (if full URLs above aren’t provided)
LOGIN_PATH <- Sys.getenv("FFA_LOGIN_PATH", unset = "")
RAW_PATH   <- Sys.getenv("FFA_RAW_STATS_PATH", unset = "/export/raw-stats.csv")

# Candidate login paths to auto-discover if none supplied
CANDIDATE_LOGIN <- c(
  "/log-in", "/login", "/signin", "/sign-in",
  "/users/sign_in", "/account/login", "/Account/Login",
  "/auth/login", "/wp-login.php", "/accounts/login/", "/member/login"
)

# ----------------- build URLs -----------------
base_url  <- normalize_base(BASE_RAW)

raw_url <- if (RAW_FULL != "") {
  normalize_path(RAW_FULL) # already full
} else {
  compose_url(base_url, if (RAW_PATH != "") RAW_PATH else "/export/raw-stats.csv")
}

login_url <- NA_character_

if (COOKIE_RAW != "") {
  # We will skip login entirely
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

if (DEBUG) {
  cat("DEBUG config:\n")
  cat("  base_url   :", base_url, "\n")
  cat("  raw_url    :", raw_url, "\n")
  cat("  login_mode :", login_mode, "\n")
  if (!is.na(login_url)) cat("  login_url  :", login_url, "\n")
}

# ----------------- fetch logic -----------------

download_raw_with_cookie <- function(cookie_header, url) {
  if (DEBUG) cat("Using cookie-only mode to GET raw CSV …\n")
  resp <- request(url) |>
    req_user_agent(ua) |>
    req_headers(Cookie = cookie_header, Referer = base_url) |>
    req_perform()
  if (resp_status(resp) >= 400) {
    stop("Raw Stats download failed with cookie: HTTP ", resp_status(resp))
  }
  resp
}

discover_login <- function(base_url, candidates) {
  if (DEBUG) cat("Auto-discovering login URL…\n")
  tried <- character(0)
  for (p in candidates) {
    u <- compose_url(base_url, p)
    tried <- c(tried, u)
    ok <- FALSE
    resp <- try(req_with(u) |> req_perform(), silent = TRUE)
    if (!inherits(resp, "try-error")) {
      status <- resp_status(resp)
      # accept 200 OK or typical 3xx redirects to actual login
      if (status < 400) {
        # must be HTML to be a login page
        ct <- tolower(resp_headers(resp)[["content-type"]] %||% "")
        if (grepl("text/html", ct)) {
          if (DEBUG) cat("  Found candidate login:", u, " (HTTP ", status, ")\n", sep = "")
          return(u)
        }
      }
    }
  }
  stop("Could not find a working login page. Tried:\n- ", paste(tried, collapse = "\n- "),
       "\nProvide FFA_LOGIN_URL or use FFA_COOKIE to bypass login.")
}

perform_login <- function(login_url, email, pass) {
  if (email == "" || pass == "") {
    stop("Missing FFA_EMAIL and/or FFA_PASSWORD. Add them as Actions secrets.")
  }

  # GET login page first (csrf, cookies)
  resp_get <- try(req_with(login_url) |> req_perform(), silent = TRUE)
  if (inherits(resp_get, "try-error") || resp_status(resp_get) >= 400) {
    stop("Could not GET login page '", login_url, "'. HTTP ",
         if (!inherits(resp_get, "try-error")) resp_status(resp_get) else "error")
  }

  html <- suppressWarnings(try(read_html(resp_body_string(resp_get)), silent = TRUE))
  csrf_name <- NULL; csrf_value <- NULL
  user_field <- NULL; pass_field <- NULL

  if (!inherits(html, "try-error")) {
    nodes <- html_elements(html, 'input[name]')
    names_all <- unique(na.omit(html_attr(nodes, "name")))
    # hidden token
    hidden_nodes <- html_elements(html, 'input[type="hidden"][name]')
    for (n in hidden_nodes) {
      nm <- html_attr(n, "name"); val <- html_attr(n, "value")
      if (!is.na(nm) && grepl("csrf|token|verification", nm, ignore.case = TRUE)) {
        csrf_name <- nm; csrf_value <- val; break
      }
    }
    user_field <- dplyr::first(c("email","username","login")[c("email","username","login") %in% names_all])
    pass_field <- dplyr::first(c("password","pass","passwd")[c("password","pass","passwd") %in% names_all])
  }

  form <- list()
  form[[ ifelse(is.null(user_field), "email", user_field) ]] <- email
  form[[ ifelse(is.null(pass_field), "password", pass_field) ]] <- pass
  if (!is.null(csrf_name) && !is.null(csrf_value)) form[[csrf_name]] <- csrf_value

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
  bin <- resp_body_raw(resp)
  writeBin(bin, out_csv)
  cat("Saved:", out_csv, " (", length(bin), " bytes)\n", sep = "")
  # try small preview
  try({
    df <- suppressWarnings(readr::read_csv(out_csv, n_max = 5, show_col_types = FALSE))
    cat("Preview columns:", paste(names(df), collapse = ", "), "\n")
  })
}

# ----------------- main -----------------
out_csv <- file.path("artifacts", "ffa_raw_stats.csv")

if (COOKIE_RAW != "") {
  resp_raw <- download_raw_with_cookie(COOKIE_RAW, raw_url)
  save_resp_to_csv(resp_raw, out_csv)
  quit(status = 0)
}

# otherwise, perform login
if (is.na(login_url)) {
  login_url <- discover_login(base_url, CANDIDATE_LOGIN)
} else if (DEBUG) {
  cat("Using provided login URL:", login_url, "\n")
}

perform_login(login_url, EMAIL, PASS)

resp_raw <- req_with(raw_url) |>
  req_headers(Referer = base_url) |>
  req_perform()

if (resp_status(resp_raw) >= 400) {
  stop("Raw Stats download failed: HTTP ", resp_status(resp_raw),
       ". If the site is an SPA with JS-only auth, set FFA_COOKIE to skip login.")
}

save_resp_to_csv(resp_raw, out_csv)
