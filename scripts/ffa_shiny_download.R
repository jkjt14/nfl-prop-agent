#!/usr/bin/env Rscript
suppressPackageStartupMessages({
  library(chromote)
  library(httr2)
  library(jsonlite)
  library(fs)
})

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

app_url <- Sys.getenv("FFA_SHINY_URL", "https://ffashiny.shinyapps.io/newApp/")
selector <- Sys.getenv("FFA_DOWNLOAD_SELECTOR", "a#projections_page-proj-download_projections-download")
timeout <- as.numeric(Sys.getenv("FFA_DOWNLOAD_TIMEOUT", "120"))
out <- Sys.getenv("FFA_OUT", "artifacts/ffa_raw_stats.csv")

message("Opening Shiny app: ", app_url)
b <- ChromoteSession$new()

on.exit({
  try(b$close(), silent = TRUE)
}, add = TRUE)

b$Network$enable()
b$Page$navigate(app_url)
b$Page$loadEventFired(wait_ = TRUE, timeout_ = timeout * 1000)

# Wait for the download link to appear and read its href
deadline <- Sys.time() + timeout
href <- NULL
while (Sys.time() < deadline) {
  res <- b$Runtime$evaluate(
    expression = sprintf(
      "(() => { const el = document.querySelector(%s); return el ? (el.href || el.getAttribute('href')) : null; })()",
      jsonlite::toJSON(selector)
    ),
    returnByValue = TRUE
  )
  href <- res$result$value
  if (!is.null(href) && nzchar(href)) break
  Sys.sleep(0.5)
}
if (is.null(href) || !nzchar(href)) {
  stop("Could not find a download link for selector: ", selector,
       ". The app may have changed its id; open the page and inspect the download button id.")
}
message("Found download href: ", href)

# Collect cookies so we can request the download URL directly
cks <- b$Network$getAllCookies()$cookies
host <- sub("^https?://([^/]+).*", "\\1", href)
cookie_pairs <- vapply(cks, function(x) {
  dom <- x$domain %||% ""
  if (nchar(dom) && grepl(host, dom, fixed = TRUE)) paste0(x$name, "=", x$value) else NA_character_
}, character(1))
cookie_pairs <- na.omit(cookie_pairs)
cookie_header <- paste(cookie_pairs, collapse = "; ")
message("Using ", length(cookie_pairs), " cookies for host ", host)

# Fetch the CSV
req <- request(href) |>
  req_headers(Cookie = cookie_header, Referer = app_url, `Accept` = "text/csv,*/*;q=0.8") |>
  req_user_agent("ffa-shiny-fetch/1.0 (+github-actions; httr2)")

resp <- req_perform(req)
status <- resp_status(resp)
if (status >= 400) stop("Download failed with HTTP ", status)

ct <- tolower(resp_headers(resp)[["content-type"]] %||% "")
payload <- resp_body_raw(resp)
head_bytes <- rawToChar(payload[seq_len(min(512L, length(payload)))])

# Guard against HTML (wrong content)
if (grepl("text/html", ct) || grepl("^<!doctype html|<html", head_bytes, ignore.case = TRUE)) {
  stop("Server returned HTML instead of CSV; selector might be wrong or the session is not authorized.")
}

dir_create(dirname(out))
writeBin(payload, out)
message("Saved CSV to: ", out, " (", length(payload), " bytes)")
cat("First 200 chars:\n", substr(rawToChar(payload), 1, 200), "\n", sep = "")
