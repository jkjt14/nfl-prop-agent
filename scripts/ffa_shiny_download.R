#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(chromote)
  library(httr2)
  library(jsonlite)
  library(fs)
})

`%||%` <- function(a, b) if (is.null(a) || is.na(a) || identical(a, "")) b else a
log <- function(...) cat(sprintf(...), "\n")

SHINY_URL    <- Sys.getenv("FFA_SHINY_URL", "")
OUT_FILE     <- Sys.getenv("FFA_OUT", "artifacts/ffa_raw_stats.csv")
TIMEOUT_SECS <- as.numeric(Sys.getenv("FFA_DOWNLOAD_TIMEOUT", "180"))
SEL          <- Sys.getenv("FFA_DOWNLOAD_SELECTOR", "")         # optional exact CSS selector
MATCH_TXT    <- tolower(Sys.getenv("FFA_DOWNLOAD_MATCH", ""))   # optional free-text bias

if (SHINY_URL == "") stop("Set FFA_SHINY_URL")

dir_create(path_dir(OUT_FILE))

# ---------------- Chrome bootstrap (explicit path & args) ----------------
chrome_path <- Sys.getenv("CHROMOTE_CHROME", "")
chrome_args <- strsplit(Sys.getenv("CHROMOTE_CHROME_ARGS", ""), "\\s+")[[1]]
chrome_args <- chrome_args[chrome_args != ""]

if (chrome_path == "") {
  # setup-chrome didn’t pass a path; hope it’s on PATH
  chrome_path <- Sys.which("chrome")
  if (!nzchar(chrome_path)) chrome_path <- Sys.which("google-chrome")
  if (!nzchar(chrome_path)) chrome_path <- Sys.which("chromium")
}
if (!nzchar(chrome_path)) {
  stop("Could not locate Chrome/Chromium. Set CHROMOTE_CHROME to an absolute path.")
}

log("Using Chrome: %s", chrome_path)
if (length(chrome_args)) log("Chrome args: %s", paste(chrome_args, collapse = " "))

# Launch Chrome with chromote’s Chrome process manager
browser <- Chrome$new(path = chrome_path, args = chrome_args)

# ---------------- Helpers ----------------
sleep_until <- function(timeout, every = 0.5, fn_check) {
  t0 <- Sys.time()
  repeat {
    if (fn_check()) return(TRUE)
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > timeout) return(FALSE)
    Sys.sleep(every)
  }
}

extract_anchors <- function(c) {
  js <- "
(() => {
  const as = Array.from(document.querySelectorAll('a'));
  return as.map(a => ({
    id: a.id || '',
    text: (a.textContent || '').trim(),
    href: a.href || ''
  }));
})()
"
  res <- c$Runtime$evaluate(list(expression = js, returnByValue = TRUE))
  out <- res$result$value
  if (is.null(out) || !length(out)) {
    return(data.frame(id=character(), text=character(), href=character()))
  }
  as.data.frame(do.call(rbind, lapply(out, function(x) {
    data.frame(
      id   = x$id   %||% "",
      text = x$text %||% "",
      href = x$href %||% "",
      stringsAsFactors = FALSE
    )
  })))
}

href_from_selector <- function(c, selector) {
  js_get <- sprintf("
(() => {
  const el = document.querySelector(%s);
  if (!el) return { ok:false, reason:'not_found', href:'' };
  const href = el.href || '';
  return { ok:true, reason:'have_el', href: href };
})()
", jsonlite::toJSON(selector, auto_unbox = TRUE))
  got <- c$Runtime$evaluate(list(expression = js_get, returnByValue = TRUE))$result$value
  if (!isTRUE(got$ok)) return(list(href = "", reason = "not_found"))
  if (nzchar(got$href) && grepl("/download/", got$href)) return(list(href = got$href, reason = "href_ready"))

  js_click <- sprintf("
(() => {
  const el = document.querySelector(%s);
  if (!el) return false;
  el.click();
  return true;
})()
", jsonlite::toJSON(selector, auto_unbox = TRUE))
  invisible(c$Runtime$evaluate(list(expression = js_click, returnByValue = TRUE)))
  Sys.sleep(2)

  got2 <- c$Runtime$evaluate(list(expression = js_get, returnByValue = TRUE))$result$value
  list(href = got2$href %||% "", reason = "after_click")
}

pick_best_candidate <- function(df, match_txt = "") {
  if (!nrow(df)) return(NULL)
  df2 <- subset(df, grepl("/download/", href, ignore.case = TRUE))
  if (!nrow(df2)) return(NULL)
  score <- rep(0L, nrow(df2))
  if (nzchar(match_txt)) {
    score <- score + as.integer(grepl(match_txt, tolower(df2$text)) | grepl(match_txt, tolower(df2$id)))
  }
  score <- score + as.integer(grepl("proj|projection|raw|csv", tolower(df2$text)))
  score <- score + as.integer(grepl("\\.csv($|\\?)", tolower(df2$href)))
  df2$._score <- score
  df2 <- df2[order(-df2$._score), , drop = FALSE]
  df2[1, , drop = FALSE]
}

download_to <- function(url, out) {
  log("Fetching CSV via HTTP: %s", url)
  resp <- request(url) |>
    req_user_agent("ffa-shiny-fetch/1.1 (+chromote+httr2)") |>
    req_timeout(TIMEOUT_SECS) |>
    req_perform()
  if (resp_status(resp) >= 400) stop(sprintf("HTTP %s while downloading CSV.", resp_status(resp)))
  writeBin(resp_body_raw(resp), out)
  log("Saved: %s (%s bytes)", out, format(file_info(out)$size, big.mark = ","))
}

# ---------------- Main ----------------
log("Opening Shiny app: %s", SHINY_URL)
c <- ChromoteSession$new(browser = browser)
on.exit(try(c$close(), silent = TRUE), add = TRUE)

c$Page$navigate(SHINY_URL)
c$Page$loadEventFired(wait_ = TRUE)

# Give the app some time to wire up widgets
sleep_until(6, 0.5, function() TRUE)

# A) exact selector path
if (nzchar(SEL)) {
  log("Trying exact selector: %s", SEL)
  h <- href_from_selector(c, SEL)
  if (nzchar(h$href) && grepl("/download/", h$href)) {
    download_to(h$href, OUT_FILE)
    quit(save = "no", status = 0)
  } else {
    log("Selector fallback (reason: %s). Auto-discovering link…", h$reason %||% "n/a")
  }
}

# B) auto-discover any /download/ link
ok <- sleep_until(TIMEOUT_SECS, 1.0, function() {
  anchors <- extract_anchors(c)
  cand <- pick_best_candidate(anchors, MATCH_TXT)
  if (!is.null(cand)) {
    download_to(cand$href[[1]], OUT_FILE)
    return(TRUE)
  }
  FALSE
})

if (!ok) {
  anchors <- extract_anchors(c)
  log("Auto-discovery failed. Anchors seen (top 25):")
  print(utils::head(anchors, 25))
  stop("Could not find a working download link. Set FFA_DOWNLOAD_SELECTOR or tweak FFA_DOWNLOAD_MATCH.")
}
