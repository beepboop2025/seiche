#!/usr/bin/env Rscript
# Dependency-light R example for Seiche's anonymous world-markets REST contract.

SEICHE_BASE_URL <- "https://api.seiche.info"
SEICHE_SECTIONS <- c(
  "summary", "money_markets", "forex", "capital_markets",
  "china_macro", "sources", "methodology", "all"
)
SEICHE_CORE_CLOCK_DOMAINS <- c("money_markets", "forex", "capital_markets")
SEICHE_CONTENT_KEYS <- c(
  "summary", "money_markets", "forex", "capital_markets",
  "china_macro", "sources", "methodology"
)
SEICHE_EXPECTED_CONTENT <- list(
  summary = "summary",
  money_markets = "money_markets",
  forex = "forex",
  capital_markets = "capital_markets",
  china_macro = "china_macro",
  sources = "sources",
  methodology = "methodology",
  all = c(
    "money_markets", "forex", "capital_markets", "china_macro",
    "sources", "methodology"
  )
)
SEICHE_CHINA_SERIES_IDS <- c(
  "CN.NBS.CPI_INDEX",
  "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY",
  "CN.NBS.MANUFACTURING_PMI",
  "CN.NBS.PPI_INDEX"
)
SEICHE_CHINA_COMMON_KEYS <- c(
  "status", "evidence_status", "as_of", "schema", "available", "dataset",
  "publisher", "source_url", "context_only", "scoring_eligible",
  "cn_cny_gauge_eligible", "values_published", "raw_evidence_included",
  "history_included", "public_distribution", "rights_status", "terms_url",
  "series_catalog", "series_count", "reading", "boundaries"
)
SEICHE_CHINA_AVAILABLE_KEYS <- c(
  SEICHE_CHINA_COMMON_KEYS, "source_registry_ids", "revision_id",
  "predecessor_revision_id", "knowledge_time", "provenance", "attestation"
)
SEICHE_CHINA_UNAVAILABLE_KEYS <- c(SEICHE_CHINA_COMMON_KEYS, "reason_code")
SEICHE_CHINA_SERIES_KEYS <- c(
  "series_id", "catalogid", "catalog_label", "row_id", "i", "ek", "ek_dp",
  "dp", "dp_name", "label", "reference_release_url", "release_url",
  "source_unit_label_exact", "source_unit_semantically_authoritative",
  "semantic_contract", "value_publication"
)
SEICHE_CHINA_SEMANTIC_KEYS <- c(
  "value_kind", "canonical_unit", "comparison_base", "transform", "threshold"
)
SEICHE_CHINA_PROVENANCE_KEYS <- c("manifest_sha256", "owner_attestation")
SEICHE_CHINA_ATTESTATION_KEYS <- c(
  "schema", "algorithm", "domain", "export_id", "signer_key_id", "signed_at",
  "manifest_sha256", "public_projection_sha256", "signature"
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

  endpoint <- paste0(
    sub("/$", "", base_url), "/api/v2/world-markets?section=",
    utils::URLencode(section, reserved = TRUE)
  )
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

is_json_object <- function(value) {
  is.list(value) && !is.null(names(value))
}

has_exact_names <- function(value, expected) {
  is_json_object(value) &&
    length(names(value)) == length(expected) &&
    !anyDuplicated(names(value)) &&
    setequal(names(value), expected)
}

require_exact_names <- function(value, expected, label) {
  if (!has_exact_names(value, expected)) {
    stop(sprintf("%s fields do not match schema v1", label))
  }
  value
}

is_scalar_string <- function(value) {
  is.character(value) && length(value) == 1L && !is.na(value)
}

is_string_or_null <- function(value) {
  is.null(value) || is_scalar_string(value)
}

is_scalar_boolean <- function(value) {
  is.logical(value) && length(value) == 1L && !is.na(value)
}

is_lower_hex <- function(value, width) {
  is_scalar_string(value) && grepl(
    sprintf("^[0-9a-f]{%d}$", width), value, perl = TRUE
  )
}

is_export_id <- function(value) {
  is_scalar_string(value) && grepl(
    "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", value, perl = TRUE
  )
}

is_canonical_utc_timestamp <- function(value) {
  if (!is_scalar_string(value) ||
      !grepl(
        "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{6})?Z$",
        value,
        perl = TRUE
      )) {
    return(FALSE)
  }
  parsed <- suppressWarnings(as.POSIXct(value, format = "%Y-%m-%dT%H:%M:%OSZ", tz = "UTC"))
  if (is.na(parsed)) {
    return(FALSE)
  }
  if (grepl("\\.", value)) {
    identical(format(parsed, "%Y-%m-%dT%H:%M:%OS6Z", tz = "UTC"), value)
  } else {
    identical(format(parsed, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"), value)
  }
}

canonical_utc_sort_key <- function(value) {
  if (!is_canonical_utc_timestamp(value)) {
    return(NULL)
  }
  if (grepl("\\.", value)) {
    value
  } else {
    sub("Z$", ".000000Z", value)
  }
}

validate_world_markets_contract <- function(payload, section) {
  if (!is_json_object(payload) ||
      !identical(payload$schema, "seiche.world-markets.v1")) {
    stop("unexpected world-markets response or schema")
  }
  if (!identical(payload$selection, section)) {
    stop("server selection does not match the request")
  }
  if (!isTRUE(payload$context_only) ||
      !is_json_object(payload$clocks) ||
      !is_scalar_string(payload$clocks$boundary) ||
      !nzchar(payload$clocks$boundary)) {
    stop("context-only or clock boundary is missing")
  }
  if (!is_json_object(payload$citation) ||
      !is_scalar_string(payload$citation$canonical_url) ||
      !nzchar(payload$citation$canonical_url)) {
    stop("citation block is missing")
  }
  if (!is_json_object(payload$scope) ||
      !identical(payload$scope$coverage_claim, "curated_partial_non_exhaustive")) {
    stop("partial-coverage boundary is missing")
  }
  validate_selector_shape(payload, section)
  validate_clock_contract(payload, section)
  if (section %in% c("china_macro", "all")) {
    validate_china_macro_contract(payload$china_macro)
  }
  invisible(TRUE)
}

validate_selector_shape <- function(payload, section) {
  present <- intersect(names(payload), SEICHE_CONTENT_KEYS)
  expected <- SEICHE_EXPECTED_CONTENT[[section]]
  if (length(present) != length(expected) || !setequal(present, expected)) {
    stop("response content does not match the requested section")
  }
  invisible(TRUE)
}

validate_clock_contract <- function(payload, section) {
  clocks <- payload$clocks
  citation <- payload$citation
  domains <- clocks$domains
  if (!has_exact_names(domains, SEICHE_CORE_CLOCK_DOMAINS)) {
    stop("world clock domains must contain only the core markets")
  }
  if (!all(c("generated_at", "as_of") %in% names(payload)) ||
      !all(c(
        "snapshot_generated_at", "latest_domain_as_of",
        "selected_evidence_as_of"
      ) %in% names(clocks)) ||
      !all(c("generated_at", "evidence_as_of") %in% names(citation))) {
    stop("required world clock paths are missing")
  }
  excluded <- clocks$excluded_from_observation_clocks
  if (!is.list(excluded) || length(excluded) != 1L ||
      !identical(excluded[[1]], "china_macro.knowledge_time")) {
    stop("China knowledge time exclusion is missing")
  }
  if (!all(vapply(domains, is_string_or_null, logical(1)))) {
    stop("world clock domain values must be strings or null")
  }
  non_null <- Filter(Negate(is.null), unname(domains))
  latest <- if (length(non_null) == 0L) NULL else max(unlist(non_null, use.names = FALSE))
  if (!identical(clocks$latest_domain_as_of, latest)) {
    stop("latest world clock is inconsistent with core domains")
  }
  selected <- NULL
  if (section %in% SEICHE_CORE_CLOCK_DOMAINS) {
    selected <- domains[[section]]
  } else if (section %in% c("summary", "all")) {
    selected <- latest
  }
  if (!identical(clocks$selected_evidence_as_of, selected) ||
      !identical(payload$as_of, selected) ||
      !identical(citation$evidence_as_of, selected)) {
    stop("selected evidence clock is inconsistent")
  }
  if (!identical(clocks$snapshot_generated_at, payload$generated_at) ||
      !identical(citation$generated_at, payload$generated_at)) {
    stop("snapshot and citation clocks are inconsistent")
  }
  if (identical(section, "china_macro") && !is.null(payload$generated_at)) {
    stop("standalone China metadata cannot borrow a snapshot clock")
  }
  invisible(TRUE)
}

validate_china_macro_contract <- function(china) {
  if (!is_json_object(china) || !is_scalar_boolean(china$available)) {
    stop("China macro availability state is inconsistent")
  }
  expected <- if (isTRUE(china$available)) {
    SEICHE_CHINA_AVAILABLE_KEYS
  } else {
    SEICHE_CHINA_UNAVAILABLE_KEYS
  }
  require_exact_names(china, expected, "China macro")
  if (!identical(china$schema, "seiche.nbs-macro-context.v1") ||
      !identical(china$dataset, "CN.NBS.MACRO_CONTEXT") ||
      !identical(china$publisher, "National Bureau of Statistics of China") ||
      !identical(
        china$source_url,
        "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData"
      ) ||
      !identical(
        china$terms_url,
        "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html"
      )) {
    stop("unexpected China macro identity or source contract")
  }
  required_false <- c(
    "cn_cny_gauge_eligible", "history_included", "raw_evidence_included",
    "scoring_eligible", "values_published"
  )
  if (!isTRUE(china$context_only) ||
      !all(vapply(
        required_false,
        function(field) identical(china[[field]], FALSE),
        logical(1)
      ))) {
    stop("China macro metadata-only boundary is missing")
  }
  if (!is.null(china$as_of) ||
      !identical(china$public_distribution, "metadata_only") ||
      !identical(china$rights_status, "redistribution_review_required")) {
    stop("China macro rights or observation boundary is invalid")
  }
  if (!is.list(china$series_catalog) ||
      !is.numeric(china$series_count) ||
      length(china$series_count) != 1L ||
      is.na(china$series_count) ||
      china$series_count != 4 ||
      length(china$series_catalog) != 4L) {
    stop("China macro series catalog is malformed")
  }
  observed_ids <- character()
  for (row in china$series_catalog) {
    require_exact_names(row, SEICHE_CHINA_SERIES_KEYS, "China macro series")
    semantic <- require_exact_names(
      row$semantic_contract,
      SEICHE_CHINA_SEMANTIC_KEYS,
      "China macro semantic contract"
    )
    if (!all(vapply(semantic, is_string_or_null, logical(1)))) {
      stop("China macro semantic metadata is malformed")
    }
    string_fields <- c(
      "series_id", "catalogid", "catalog_label", "row_id", "i", "ek", "ek_dp",
      "dp", "label", "reference_release_url", "release_url"
    )
    if (!all(vapply(row[string_fields], is_scalar_string, logical(1))) ||
        !all(vapply(
          row[c("dp_name", "source_unit_label_exact")],
          is_string_or_null,
          logical(1)
        ))) {
      stop("China macro series metadata is malformed")
    }
    if (!is_scalar_boolean(row$source_unit_semantically_authoritative) ||
        !identical(
          row$value_publication,
          "withheld_pending_rights_review"
        )) {
      stop("China macro series publication gate is invalid")
    }
    observed_ids <- c(observed_ids, row$series_id)
  }
  if (!identical(observed_ids, SEICHE_CHINA_SERIES_IDS)) {
    stop("China macro series identities or order drifted")
  }
  if (!is.list(china$boundaries) || length(china$boundaries) != 3L ||
      !all(vapply(
        china$boundaries,
        function(item) is_scalar_string(item) && nzchar(item),
        logical(1)
      )) ||
      !is_scalar_string(china$reading)) {
    stop("China macro public boundaries are malformed")
  }
  valid_state <- (
    isTRUE(china$available) &&
      identical(china$status, "restricted") &&
      identical(china$evidence_status, "restricted")
  ) || (
    identical(china$available, FALSE) &&
      identical(china$status, "structural") &&
      identical(china$evidence_status, "unavailable")
  )
  if (!valid_state) {
    stop("China macro availability state is inconsistent")
  }
  if (!isTRUE(china$available)) {
    if (!identical(china$reason_code, "signed_owner_export_required")) {
      stop("China macro unavailable reason is invalid")
    }
    return(invisible(TRUE))
  }
  source_ids <- unlist(china$source_registry_ids, use.names = FALSE)
  knowledge_key <- canonical_utc_sort_key(china$knowledge_time)
  if (!is_export_id(china$revision_id) ||
      (!is.null(china$predecessor_revision_id) &&
       !is_export_id(china$predecessor_revision_id)) ||
      is.null(knowledge_key) ||
      !is.list(china$source_registry_ids) ||
      !identical(
        source_ids,
        c("nbs_monthly_data_browser", "nbs_terms_of_service")
      )) {
    stop("available China macro revision metadata is malformed")
  }
  provenance <- require_exact_names(
    china$provenance,
    SEICHE_CHINA_PROVENANCE_KEYS,
    "China macro provenance"
  )
  if (!is_lower_hex(provenance$manifest_sha256, 64L) ||
      !identical(provenance$owner_attestation, "ed25519")) {
    stop("China macro provenance is malformed")
  }
  attestation <- require_exact_names(
    china$attestation,
    SEICHE_CHINA_ATTESTATION_KEYS,
    "China macro attestation"
  )
  signed_key <- canonical_utc_sort_key(attestation$signed_at)
  if (!identical(attestation$schema, "seiche.nbs-owner-export-signature.v1") ||
      !identical(attestation$algorithm, "ed25519") ||
      !identical(attestation$domain, "seiche-nbs-owner-export-v1") ||
      !identical(attestation$export_id, china$revision_id) ||
      !identical(attestation$manifest_sha256, provenance$manifest_sha256) ||
      !is_lower_hex(attestation$signer_key_id, 64L) ||
      !is_lower_hex(attestation$public_projection_sha256, 64L) ||
      !is_lower_hex(attestation$signature, 128L) ||
      is.null(signed_key) ||
      signed_key < knowledge_key) {
    stop("China macro attestation is malformed")
  }
  invisible(TRUE)
}

contract_receipt <- function(
  payload,
  timeout_seconds = SEICHE_TIMEOUT_SECONDS,
  max_response_bytes = SEICHE_MAX_RESPONSE_BYTES
) {
  if (timeout_seconds <= 0 || max_response_bytes <= 0) {
    stop("timeout_seconds and max_response_bytes must be positive")
  }
  list(
    schema = payload$schema,
    selection = payload$selection,
    status = payload$status,
    clocks = payload$clocks,
    citation = payload$citation,
    scope = payload$scope,
    client_limits = list(
      timeout_seconds = timeout_seconds,
      max_response_bytes = max_response_bytes,
      automatic_retries = 0L
    )
  )
}

if (sys.nframe() == 0L) {
  print(contract_receipt(fetch_world_markets()))
}
