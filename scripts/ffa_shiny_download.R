#!/usr/bin/env Rscript
# Download the “Raw Stats / Projections” CSV from a Shiny app using Chromote (headless Chrome).
# Reads configuration from environment variables:
#   FFA_SHINY_URL            (required) e.g. https://ffashiny.shinyapps.io/newApp/
#   FFA_DOWNLOAD_SELECTOR    (required) e.g. 'a#projections_page-proj-download_projections-download'
#   FFA_DOWNLOAD_TIMEOUT     (optional) seconds, default 120
#   FFA_OUT                  (optional) output csv path, default artifacts/ffa_raw_stats.csv
#   CHROMOTE_CHROME          (optional) absolute path to Chrome binary (set by setup-chrome action)

# ---------- tiny auto-installer (safe locally; CI already installs) ----------
needed <- c("chromote","fs","jsonlite")
miss <- needed[!vapply(needed, requireNamespace, logical(1), quietly = TRUE)]
if (length(miss)) install.packages(miss, repos = "https://cloud.r-project.org")

suppressPackageStartupMessages({
  library(chromote)
  library(fs)
  library(jsonlite)
})

# ---------- config ----------
url       <- Sys.getenv("FFA_SHINY_URL", "")
selector  <- Sys.getenv("FFA_DOWNLOAD_SELECTOR", "")
timeout_s <- as.numeric(Sys.getenv("FFA_DOWNLOAD_TIMEOUT", "120"))
out_csv   <- Sys.getenv("FFA_OUT", "artifacts/ffa_raw_stats.csv")

if (url == "" || selector == "") {
  stop("FFA_SHINY_URL and FFA_DOWNLOAD_SELECTOR are required.")
}

dir_create(path_dir(out_csv))
dl_dir <- fs::path_temp("ffa_dls")
dir_create(dl_dir)

# ---------- start chromote ----------
# Use the Chrome installed by setup-chrome if provided
chrome_path <- Sys.getenv("CHROMOTE_CHROME", unset = NA_character_)
b <- if (!is.na(chrome_path) && nzchar(chrome_path)) {
  ChromoteSession$new(chrome_args = c(
    paste0("--user-data-dir=", fs::path_temp("ffa_ud")),
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
    "--disable-gpu"
  ), chrome_executable = chrome_path)
} else {
  ChromoteSession$new(chrome_args = c(
    paste0("--user-data-dir=", fs::path_temp("ffa_ud")),
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
    "--disable-gpu"
  ))
}

on.exit({
  try(b$close(), silent = TRUE)
  try(b$close_browser(), silent = TRUE)
}, add = TRUE)

# Allow headless downloads to a known folder (API may differ by Chrome version; wrap in try)
try({
  b$Browser$setDownloadBehavior(
    behavior = "allow",
    downloadPath = dl_dir,
    eventsEnabled = TRUE
  )
}, silent = TRUE)

# ---------- helpers ----------
wait_for_ready <- function(session, max_wait = 60) {
  t0 <- Sys.time()
  repeat {
    # document.readyState: "loading" | "interactive" | "complete"
    res <- session$Runtime$evaluate(
      expression = "document.readyState",
      returnByValue = TRUE
    )
    state <- tryCatch(res$result$value, error = function(e) "loading")
    if (identical(state, "complete")) break
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > max_wait) {
      stop("Timed out waiting for DOM ready")
    }
    Sys.sleep(0.5)
  }
}

click_selector <- function(session, css) {
  js <- sprintf(
    "(() => { const el = document.querySelector(%s); if (!el) return 'NOT_FOUND'; el.click(); return 'CLICKED'; })();",
    jsonlite::toJSON(css, auto_unbox = TRUE)
  )
  res <- session$Runtime$evaluate(expression = js, returnByValue = TRUE)
  val <- tryCatch(res$result$value, error = function(e) "ERROR")
  if (identical(val, "NOT_FOUND")) stop("Download element not found: ", css)
  if (identical(val, "ERROR"))     stop("JS evaluation error while clicking: ", css)
  invisible(TRUE)
}

wait_for_download <- function(dir, start_files, max_wait = 120) {
  t0 <- Sys.time()
  repeat {
    Sys.sleep(0.5)
    files <- dir(dir, all.files = FALSE, full.names = TRUE)
    newf  <- setdiff(files, start_files)
    # Ignore temporary Chrome *.crdownload; look for completed file
    done  <- newf[!grepl("\\.crdownload$", newf)]
    if (length(done)) return(done[[1]])
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > max_wait) {
      stop("Timed out waiting for download to finish")
    }
  }
}

# ---------- flow ----------
# Navigate (avoid using event callbacks; stick to polling to prevent 'apply non-function' mistakes)
b$Page$enable()
b$Network$enable()
b$Page$navigate(url)
wait_for_ready(b, max_wait = 90)

# Give Shiny a moment to render reactive UI
Sys.sleep(3)

# Snapshot pre-download files
pre <- dir(dl_dir, all.files = FALSE, full.names = TRUE)

# Click the download button/link
click_selector(b, selector)

# Wait for file
csv_path <- wait_for_download(dl_dir, pre, max_wait = timeout_s)

# Move to requested output
file_move(csv_path, out_csv)

# Optional: print first line(s) for the logs
cat("Saved:", out_csv, "\n")
try({
  # Just peek at header
  cat("Header preview:\n")
  con <- file(out_csv, open = "r", encoding = "UTF-8")
  on.exit(close(con), add = TRUE)
  for (i in 1:3) {
    ln <- readLines(con, n = 1, warn = FALSE)
    if (length(ln) == 0) break
    cat(ln, "\n", sep = "")
  }
}, silent = TRUE)
