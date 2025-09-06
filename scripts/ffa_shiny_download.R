# Auto-install required packages if missing
needed <- c("chromote", "fs", "jsonlite")
miss   <- needed[!vapply(needed, requireNamespace, logical(1), quietly = TRUE)]
if (length(miss)) install.packages(miss, repos = "https://cloud.r-project.org")

suppressPackageStartupMessages({
  library(chromote)
  library(fs)
  library(jsonlite)
})

shiny_url <- Sys.getenv("FFA_SHINY_URL", "https://ffashiny.shinyapps.io/newApp/")
download_selector <- Sys.getenv(
  "FFA_DOWNLOAD_SELECTOR",
  "a#projections_page-proj-download_projections-download"
)
download_timeout <- as.integer(Sys.getenv("FFA_DOWNLOAD_TIMEOUT", "90"))
out_csv <- Sys.getenv("FFA_OUT", "artifacts/ffa_raw_stats.csv")

dir_create(path_dir(out_csv))
download_dir <- path_abs(path_dir(out_csv))

sess <- ChromoteSession$new()
on.exit({ try(sess$close(), silent = TRUE) }, add = TRUE)

# Allow downloads to our artifacts folder
sess$Browser$setDownloadBehavior(
  behavior = "allow",
  downloadPath = download_dir
)

# Navigate and wait for page load
sess$Page$navigate(shiny_url)
sess$Page$waitForLoadEventFired()

# Wait for the download link to exist
wait_js <- sprintf("
  (function(){
    const sel = %s;
    return new Promise((resolve,reject)=>{
      const t0 = Date.now();
      const max = %d * 1000;
      function tick(){
        const el = document.querySelector(sel);
        if(el){ resolve(true); return; }
        if(Date.now()-t0 > max){ reject('timeout'); return; }
        setTimeout(tick, 250);
      }
      tick();
    });
  })();
", jsonlite::toJSON(download_selector, auto_unbox = TRUE), download_timeout)

sess$Runtime$evaluate(wait_js)

# Click the download link
click_js <- sprintf("
  (function(){
    const el = document.querySelector(%s);
    if(!el) throw new Error('Download element not found');
    el.click();
    return true;
  })();
", jsonlite::toJSON(download_selector, auto_unbox = TRUE))

sess$Runtime$evaluate(click_js)

# Wait for a CSV to appear in download_dir, then move/rename to out_csv
t0 <- Sys.time()
found <- FALSE
repeat {
  Sys.sleep(1)
  csvs <- dir(download_dir, pattern = "\\.csv$", full.names = TRUE)
  if (length(csvs)) {
    # pick the newest file
    newest <- csvs[which.max(file_info(csvs)$modification_time)]
    file_move(newest, out_csv)
    found <- TRUE
    break
  }
  if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > download_timeout) break
}

if (!found || !file_exists(out_csv)) {
  stop("Download did not complete within ", download_timeout, "s or no CSV found.")
}

cat("Saved CSV to: ", out_csv, "\n", sep = "")
