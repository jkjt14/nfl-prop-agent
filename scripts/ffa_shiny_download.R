#!/usr/bin/env Rscript
# Fetch CSV from a Shiny app by clicking its Download button in headless Chrome.
# No Odds API usage. Works on GitHub Actions and locally.
#
# ENV VARS:
#   FFA_SHINY_URL         (required) e.g. https://ffashiny.shinyapps.io/newApp/
#   FFA_DOWNLOAD_SELECTOR (required) e.g. 'a#projections_page-proj-download_projections-download'
#   FFA_DOWNLOAD_TIMEOUT  (optional) default 120 (seconds)
#   FFA_OUT               (optional) default artifacts/ffa_raw_stats.csv
#   CHROMOTE_CHROME       (optional) absolute path to Chrome (Actions step sets this)

needed <- c("chromote","fs","jsonlite")
miss <- needed[!vapply(needed, requireNamespace, logical(1), quietly = TRUE)]
if (length(miss)) install.packages(miss, repos = "https://cloud.r-project.org")

suppressPackageStartupMessages({
  library(chromote)
  library(fs)
  library(jsonlite)
})

# ------------- config -------------
url       <- Sys.getenv("FFA_SHINY_URL", "")
selector  <- Sys.getenv("FFA_DOWNLOAD_SELECTOR", "")
timeout_s <- as.numeric(Sys.getenv("FFA_DOWNLOAD_TIMEOUT", "120"))
out_csv   <- Sys.getenv("FFA_OUT", "artifacts/ffa_raw_stats.csv")
if (!nzchar(url) || !nzchar(selector)) {
  stop("FFA_SHINY_URL and FFA_DOWNLOAD_SELECTOR are required.")
}

dir_create(path_dir(out_csv))
dl_dir <- fs::path_temp("ffa_dls"); dir_create(dl_dir)

# Hint chromote where Chrome is (compatible way)
chrome_path <- Sys.getenv("CHROMOTE_CHROME", "")
if (nzchar(chrome_path)) Sys.setenv(CHROME_BIN = chrome_path)

# ------------- start browser/session -------------
# Don’t pass chrome_args/chrome_executable to ChromoteSession$new() (not supported in your version)
b <- ChromoteSession$new()

on.exit({
  try(b$close(), silent = TRUE)
  try(b$close_browser(), silent = TRUE)
}, add = TRUE)

# Allow downloads in headless (try both modern & legacy CDP)
suppressWarnings({
  try(b$Browser$setDownloadBehavior(behavior = "allow",
                                    downloadPath = dl_dir,
                                    eventsEnabled = TRUE), silent = TRUE)
  try(b$Page$setDownloadBehavior(behavior = "allow",
                                 downloadPath = dl_dir), silent = TRUE)
})

# ------------- helpers -------------
wait_for_ready <- function(session, max_wait = 90) {
  t0 <- Sys.time()
  repeat {
    res <- session$Runtime$evaluate(expression = "document.readyState", returnByValue = TRUE)
    state <- tryCatch(res$result$value, error = function(e) "loading")
    if (identical(state, "complete")) break
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > max_wait)
      stop("Timed out waiting for DOM ready")
    Sys.sleep(0.5)
  }
}

# Some shinyapps.io wrappers use an iframe. If present, navigate directly into it.
maybe_enter_iframe <- function(session) {
  js <- '(() => {
    const f = document.querySelector("iframe[src*=\'/_w_\']") || document.querySelector("iframe");
    return f ? (f.src || null) : null;
  })();'
  res <- session$Runtime$evaluate(expression = js, returnByValue = TRUE)
  src <- tryCatch(res$result$value, error = function(e) NULL)
  if (is.null(src) || !nzchar(src)) return(invisible(FALSE))
  session$Page$navigate(src)
  wait_for_ready(session, max_wait = 90)
  Sys.sleep(2)
  invisible(TRUE)
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
    files <- dir(dir, full.names = TRUE, all.files = FALSE)
    newf  <- setdiff(files, start_files)
    done  <- newf[!grepl("\\.crdownload$", newf)]
    if (length(done)) return(done[[1]])
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > max_wait)
      stop("Timed out waiting for download to finish")
  }
}

# ------------- flow -------------
b$Page$enable(); b$Network$enable()
b$Page$navigate(url)
wait_for_ready(b, max_wait = 90)
Sys.sleep(3)

# If wrapped, dive into the iframe so the selector exists
in_iframe <- FALSE
try({ in_iframe <- maybe_enter_iframe(b) }, silent = TRUE)

pre <- dir(dl_dir, full.names = TRUE, all.files = FALSE)

# Try click; if not found and we didn’t enter iframe, try entering and click again
clicked <- FALSE
try({
  click_selector(b, selector)
  clicked <- TRUE
}, silent = TRUE)

if (!clicked && !in_iframe) {
  if (maybe_enter_iframe(b)) {
    click_selector(b, selector)
    clicked <- TRUE
  }
}

if (!clicked) stop("Could not click the download element. Check FFA_DOWNLOAD_SELECTOR.")

csv_path <- wait_for_download(dl_dir, pre, max_wait = timeout_s)
file_move(csv_path, out_csv)

cat("Saved:", out_csv, "\n")
try({
  cat("Header preview:\n")
  con <- file(out_csv, open = "r", encoding = "UTF-8"); on.exit(close(con), add = TRUE)
  for (i in 1:3) { ln <- readLines(con, n = 1, warn = FALSE); if (!length(ln)) break; cat(ln, "\n") }
}, silent = TRUE)
