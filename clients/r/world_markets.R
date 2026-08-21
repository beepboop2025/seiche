#!/usr/bin/env Rscript
# Dependency-light R example for Seiche's anonymous world-markets REST contract.

SEICHE_BASE_URL <- "https://api.seiche.info"
SEICHE_SECTIONS <- c(
  "summary", "money_markets", "forex", "capital_markets",
  "sources", "methodology", "all"
)
SEICHE_TIMEOUT_SECONDS <- 15
SEICHE_MAX_RESPONSE_BYTES <- 2000000L

fetch_world_markets <- function(
  section = "sources",
  base_url = SEICHE_BASE_URL,
  timeout_seconds = SEICHE_TIMEOUT_SECONDS,
  max_response_bytes = SEICHE_MAX_RESPONSE_BYTES
) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("Install the single runtime dependency with install.packages('jsonlite')")
  }
  if (!(section %in% SEICHE_SECTIONS)) {
    stop(sprintf("section must be one of: %s", paste(SEICHE_SECTIONS, collapse = ", ")))
  }
  if (timeout_seconds <= 0 || max_response_bytes <= 0) {
    stop("timeout_seconds and max_response_bytes must be positive")
  }

  endpoint <- paste0(sub("/$", "", base_url), "/api/v2/world-markets?section=",
                     utils::URLencode(section, reserved = TRUE))
  previous_timeout <- getOption("timeout")
  options(timeout = timeout_seconds)
  on.exit(options(timeout = previous_timeout), add = TRUE)
  connection <- url(endpoint, open = "rb", headers = c(Accept = "application/json"))
  on.exit(close(connection), add = TRUE)
  bytes <- readBin(connection, what = "raw", n = max_response_bytes + 1L)
  if (length(bytes) > max_response_bytes) {
    stop(sprintf("response exceeded the %d-byte client limit", max_response_bytes))
  }

  payload <- jsonlite::fromJSON(rawToChar(bytes), simplifyVector = FALSE)
  validate_world_markets_contract(payload, section)
  payload
}

validate_world_markets_contract <- function(payload, section) {
  if (!is.list(payload) || !identical(payload$schema, "seiche.world-markets.v1")) {
    stop("unexpected world-markets response or schema")
  }
  if (!identical(payload$selection, section)) {
    stop("server selection does not match the request")
  }
  if (!isTRUE(payload$context_only) || is.null(payload$clocks$boundary)) {
    stop("context-only or clock boundary is missing")
  }
  if (is.null(payload$citation$canonical_url)) {
    stop("citation block is missing")
  }
  if (!identical(payload$scope$coverage_claim, "curated_partial_non_exhaustive")) {
    stop("partial-coverage boundary is missing")
  }
  invisible(TRUE)
}

contract_receipt <- function(payload) {
  list(
    schema = payload$schema,
    selection = payload$selection,
    status = payload$status,
    clocks = payload$clocks,
    citation = payload$citation,
    scope = payload$scope,
    client_limits = list(
      timeout_seconds = SEICHE_TIMEOUT_SECONDS,
      max_response_bytes = SEICHE_MAX_RESPONSE_BYTES,
      automatic_retries = 0L
    )
  )
}

if (sys.nframe() == 0L) {
  print(contract_receipt(fetch_world_markets()))
}
