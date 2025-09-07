#!/usr/bin/env Rscript
# Robust Shiny CSV fetch via headless Chrome (chromote).
# - Auto-enters iframe if present
# - (Optional) clicks a nav/tab by visible text before searching for the download button
# - Auto-discovers a shiny download element if an explicit selector fails
# - Waits for the file to fully download, then moves it to artifacts

needed <- c("chromote","fs","jsonlite")
miss <- needed[!vapply(needed, requireNamespace, logical(1), quietly = TRUE)]
if (length(miss)) install.packages(miss, repos = "https://cloud.r-project.org")

suppressPackageStartupMessages({
  library(chromote)
  library(fs)
  library(jsonlite)
})

# ---------------- config ----------------
url        <- Sys.getenv("FFA_SHINY_URL", "")
selector   <- Sys.getenv("FFA_DOWNLOAD_SELECTOR", "")   # optional now
timeout_s  <- as.numeric(Sys.getenv("FFA_DOWNLOAD_TIMEOUT", "150"))
out_csv    <- Sys.getenv("FFA_OUT", "artifacts/ffa_raw_stats.csv")
preclick   <- Sys.getenv("FFA_PRECLICK_TEXT", "")       # e.g. "Projections" or "Proj"
if (!nzchar(url)) stop("FFA_SHINY_URL is required.")

dir_create(path_dir(out_csv))
dl_dir <- fs::path_temp("ffa_dls"); dir_create(dl_dir)

# Allow Actions to hint chromote where Chrome is
chrome_path <- Sys.getenv("CHROMOTE_CHROME", "")
if (nzchar(chrome_path)) Sys.setenv(CHROME_BIN = chrome_path)

# ---------------- helpers ----------------
logln <- function(...) cat(paste0(..., "\n"))

wait_dom_ready <- function(session, max_wait = 90) {
  t0 <- Sys.time()
  repeat {
    res <- session$Runtime$evaluate(expression = "document.readyState", returnByValue = TRUE)
    ready <- tryCatch(identical(res$result$value, "complete"), error = function(e) FALSE)
    if (ready) break
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > max_wait)
      stop("Timed out waiting for DOM ready")
    Sys.sleep(0.5)
  }
}

maybe_enter_iframe <- function(session) {
  js <- '(() => {
    const cand = document.querySelector("iframe[src*=\'/_w_\']") || document.querySelector("iframe");
    return cand ? cand.src : null;
  })();'
  res <- session$Runtime$evaluate(expression = js, returnByValue = TRUE)
  src <- tryCatch(res$result$value, error = function(e) NULL)
  if (!is.null(src) && nzchar(src)) {
    logln("Entering iframe: ", src)
    session$Page$navigate(src)
    wait_dom_ready(session, 90)
    Sys.sleep(2)
    return(TRUE)
  }
  FALSE
}

click_by_visible_text <- function(session, text_pat) {
  if (!nzchar(text_pat)) return(invisible(FALSE))
  js <- sprintf('(() => {
    const re = new RegExp(%s, "i");
    const els = Array.from(document.querySelectorAll("a,button,li,div,span"));
    const hit = els.find(el => re.test((el.innerText||"").trim()));
    if (!hit) return { ok:false, err:"NOT_FOUND" };
    hit.click();
    return { ok:true, id: hit.id || null, tag: hit.tagName, txt: (hit.innerText||"").trim().slice(0,80) };
  })();', jsonlite::toJSON(text_pat, auto_unbox = TRUE))
  r <- session$Runtime$evaluate(expression = js, returnByValue = TRUE)
  val <- tryCatch(r$result$value, error = function(e) list(ok=FALSE,err="EVAL_ERR"))
  if (isTRUE(val$ok)) {
    logln("Pre-clicked element: tag=", val$tag, " id=", val$id, " text='", val$txt, "'")
    Sys.sleep(2)
    return(TRUE)
  } else {
    logln("Pre-click text not found: ", text_pat)
    return(FALSE)
  }
}

click_selector <- function(session, css) {
  js <- sprintf(
    "(() => { const el = document.querySelector(%s); if (!el) return 'NOT_FOUND'; el.click(); return 'CLICKED'; })();",
    jsonlite::toJSON(css, auto_unbox = TRUE)
  )
  res <- session$Runtime$evaluate(expression = js, returnByValue = TRUE)
  val <- tryCatch(res$result$value, error = function(e) "ERROR")
  if (identical(val, "NOT_FOUND")) stop("Element not found for selector: ", css)
  if (identical(val, "ERROR"))     stop("JS error while clicking selector: ", css)
  invisible(TRUE)
}

discover_and_click_download <- function(session) {
  # Find likely download links/buttons and click the first "shiny-download-link" if present
  js <- '
(() => {
  const cand = [];
  const all = Array.from(document.querySelectorAll("a,button"));
  for (const el of all) {
    const id = el.id || "";
    const cls = (el.className||"").toString();
    const txt = (el.innerText || "").trim();
    if (/shiny-download-link/.test(cls) || /download/i.test(id) || /download/i.test(txt)) {
      cand.push({id, cls, txt, tag: el.tagName});
    }
  }
  // prefer shiny-download-link
  cand.sort((a,b) => {
    const aw = /shiny-download-link/.test(a.cls) ? 0 : 1;
    const bw = /shiny-download-link/.test(b.cls) ? 0 : 1;
    return aw - bw;
  });
  if (cand.length === 0) return {ok:false, found:[]};
  // click first candidate
  const first = cand[0];
  const el = document.getElementById(first.id) || all.find(e => (e.innerText||"").trim() === first.txt);
  if (!el) return {ok:false, found:cand};
  el.click();
  return {ok:true, clicked:first, found:cand};
})();'
  res <- session$Runtime$evaluate(expression = js, returnByValue = TRUE)
  val <- tryCatch(res$result$value, error = function(e) list(ok=FALSE,found=list()))
  # Log what we saw to help debugging
  if (isTRUE(val$ok)) {
    logln("Auto-discovered and clicked download:", 
          " tag=", val$clicked$tag, 
          " id=", val$clicked$id, 
          " class=", val$clicked$cls, 
          " text='", val$clicked$txt, "'")
    return(TRUE)
  } else {
    logln("No download element auto-discovered. Candidates seen:")
    if (length(val$found)) {
      for (x in val$found) {
        logln("  - tag=", x$tag, " id=", x$id, " class=", x$cls, " text='", x$txt, "'")
      }
    } else {
      logln("  (none)")
    }
    return(FALSE)
  }
}

wait_for_download <- function(dir, pre_files, max_wait = 150) {
  t0 <- Sys.time()
  repeat {
    Sys.sleep(0.5)
    files <- dir(dir, full.names = TRUE, all.files = FALSE)
    newf  <- setdiff(files, pre_files)
    # ignore partial Chrome temp files
    done  <- newf[!grepl("\\.crdownload$", newf, ignore.case = TRUE)]
    if (length(done)) return(done[[1]])
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > max_wait)
      stop("Timed out waiting for download")
  }
}

# ---------------- run ----------------
b <- ChromoteSession$new()
on.exit({
  try(b$close(), silent = TRUE)
  try(b$close_browser(), silent = TRUE)
}, add = TRUE)

# Allow downloads (modern & legacy)
suppressWarnings({
  try(b$Browser$setDownloadBehavior(behavior = "allow", downloadPath = dl_dir, eventsEnabled = TRUE), silent = TRUE)
  try(b$Page$setDownloadBehavior(behavior = "allow", downloadPath = dl_dir), silent = TRUE)
})

b$Page$enable(); b$Network$enable()
logln("Navigating to: ", url)
b$Page$navigate(url)
wait_dom_ready(b, 90)
Sys.sleep(3)

# if the app is in an iframe, go inside
in_iframe <- FALSE
try({ in_iframe <- maybe_enter_iframe(b) }, silent = TRUE)

# (optional) click a nav/tab first, e.g. "Projections"
if (nzchar(preclick)) {
  click_by_visible_text(b, preclick)
  Sys.sleep(2)
}

pre <- dir(dl_dir, full.names = TRUE, all.files = FALSE)

clicked <- FALSE
# 1) explicit selector if provided
if (nzchar(selector)) {
  logln("Trying explicit selector: ", selector)
  clicked <- tryCatch({ click_selector(b, selector); TRUE }, error = function(e) { logln("  explicit selector failed: ", e$message); FALSE })
}

# 2) auto-discover
if (!clicked) {
  logln("Attempting auto-discovery of download element…")
  clicked <- discover_and_click_download(b)
}

# 3) if still not clicked and we didn't enter iframe at start, try entering now then retry
if (!clicked && !in_iframe) {
  if (maybe_enter_iframe(b)) {
    if (nzchar(preclick)) click_by_visible_text(b, preclick)
    if (nzchar(selector)) {
      clicked <- tryCatch({ click_selector(b, selector); TRUE }, error = function(e) FALSE)
    }
    if (!clicked) clicked <- discover_and_click_download(b)
  }
}

if (!clicked) stop("Could not click any download element. Set FFA_PRECLICK_TEXT to the panel name (e.g. 'Projections'), or update FFA_DOWNLOAD_SELECTOR.")

csv_path <- wait_for_download(dl_dir, pre, timeout_s)
file_move(csv_path, out_csv)
logln("Saved: ", out_csv)

# quick peek
try({
  logln("Header preview:")
  con <- file(out_csv, open = "r", encoding = "UTF-8"); on.exit(close(con), add = TRUE)
  for (i in 1:3) { ln <- readLines(con, n = 1, warn = FALSE); if (!length(ln)) break; logln(ln) }
}, silent = TRUE)
