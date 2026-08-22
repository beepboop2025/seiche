#!/usr/bin/env Rscript
# Offline semantic-boundary tests for the dependency-light R example client.

script_arg <- commandArgs(trailingOnly = FALSE)
script_path <- sub("^--file=", "", script_arg[grepl("^--file=", script_arg)][1])
source(file.path(dirname(normalizePath(script_path)), "world_markets.R"))

series_row <- function(series_id) {
  list(
    series_id = series_id,
    catalogid = "catalog-id",
    catalog_label = "Catalog label",
    row_id = "row-id",
    i = "indicator-id",
    ek = "export-key",
    ek_dp = "export-key-dimension",
    dp = "1",
    dp_name = "dimension",
    label = "Series label",
    reference_release_url = paste0(
      "https://www.stats.gov.cn/english/PressRelease/202608/",
      "t20260810_1965018.html"
    ),
    release_url = paste0(
      "https://www.stats.gov.cn/english/PressRelease/202608/",
      "t20260810_1965018.html"
    ),
    source_unit_label_exact = "%",
    source_unit_semantically_authoritative = TRUE,
    semantic_contract = list(
      value_kind = "index_level",
      canonical_unit = "index_points",
      comparison_base = NULL,
      transform = NULL,
      threshold = NULL
    ),
    value_publication = "withheld_pending_rights_review"
  )
}

china_macro <- function(available = TRUE) {
  common <- list(
    schema = "seiche.nbs-macro-context.v1",
    dataset = "CN.NBS.MACRO_CONTEXT",
    publisher = "National Bureau of Statistics of China",
    source_url = paste0(
      "https://data.stats.gov.cn/dg/website/page.html#/pc/national/",
      "en/monthData"
    ),
    terms_url = "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html",
    status = if (available) "restricted" else "structural",
    evidence_status = if (available) "restricted" else "unavailable",
    available = available,
    as_of = NULL,
    context_only = TRUE,
    scoring_eligible = FALSE,
    cn_cny_gauge_eligible = FALSE,
    values_published = FALSE,
    raw_evidence_included = FALSE,
    history_included = FALSE,
    public_distribution = "metadata_only",
    rights_status = "redistribution_review_required",
    series_catalog = lapply(SEICHE_CHINA_SERIES_IDS, series_row),
    series_count = 4L,
    reading = "Metadata-only China macro context.",
    boundaries = list("owner", "values", "scoring")
  )
  if (!available) {
    return(c(common, list(reason_code = "signed_owner_export_required")))
  }
  c(common, list(
    revision_id = "nbs-2026-07-r1",
    predecessor_revision_id = NULL,
    knowledge_time = "2026-08-10T02:00:00Z",
    source_registry_ids = list(
      "nbs_monthly_data_browser",
      "nbs_terms_of_service"
    ),
    provenance = list(
      manifest_sha256 = paste(rep("a", 64L), collapse = ""),
      owner_attestation = "ed25519"
    ),
    attestation = list(
      schema = "seiche.nbs-owner-export-signature.v1",
      algorithm = "ed25519",
      domain = "seiche-nbs-owner-export-v1",
      export_id = "nbs-2026-07-r1",
      signer_key_id = paste(rep("c", 64L), collapse = ""),
      signed_at = "2026-08-10T02:05:00Z",
      manifest_sha256 = paste(rep("a", 64L), collapse = ""),
      public_projection_sha256 = paste(rep("d", 64L), collapse = ""),
      signature = paste(rep("e", 128L), collapse = "")
    )
  ))
}

payload_for <- function(selection = "sources") {
  china_only <- identical(selection, "china_macro")
  generated_at <- if (china_only) NULL else "2026-08-21T20:54:06Z"
  domains <- if (china_only) {
    list(money_markets = NULL, forex = NULL, capital_markets = NULL)
  } else {
    list(
      money_markets = "2026-08-20",
      forex = "2026-08-19",
      capital_markets = "2026-08-18"
    )
  }
  latest <- if (china_only) NULL else "2026-08-20"
  selected <- if (selection %in% c("summary", "all")) {
    latest
  } else if (selection %in% SEICHE_CORE_CLOCK_DOMAINS) {
    domains[[selection]]
  } else {
    NULL
  }
  payload <- list(
    schema = "seiche.world-markets.v1",
    selection = selection,
    generated_at = generated_at,
    as_of = selected,
    context_only = TRUE,
    clocks = list(
      boundary = "Response time never advances a source clock.",
      domains = domains,
      snapshot_generated_at = generated_at,
      latest_domain_as_of = latest,
      selected_evidence_as_of = selected,
      excluded_from_observation_clocks = list("china_macro.knowledge_time")
    ),
    citation = list(
      canonical_url = "https://seiche.info/world-markets",
      generated_at = generated_at,
      evidence_as_of = selected
    ),
    scope = list(coverage_claim = "curated_partial_non_exhaustive")
  )
  if (identical(selection, "summary")) {
    payload$summary <- list()
  } else if (selection %in% SEICHE_CORE_CLOCK_DOMAINS) {
    payload[[selection]] <- list()
  } else if (identical(selection, "china_macro")) {
    payload$china_macro <- china_macro()
  } else if (identical(selection, "sources")) {
    payload$sources <- list()
  } else if (identical(selection, "methodology")) {
    payload$methodology <- list()
  } else {
    payload$money_markets <- list()
    payload$forex <- list()
    payload$capital_markets <- list()
    payload$china_macro <- china_macro()
    payload$sources <- list()
    payload$methodology <- list()
  }
  payload
}

expect_rejected <- function(payload, section = payload$selection) {
  stopifnot(inherits(
    try(validate_world_markets_contract(payload, section), silent = TRUE),
    "try-error"
  ))
}

stopifnot(isTRUE(validate_world_markets_contract(payload_for("sources"), "sources")))
stopifnot(isTRUE(validate_world_markets_contract(payload_for("china_macro"), "china_macro")))

unavailable <- payload_for("china_macro")
unavailable$china_macro <- china_macro(FALSE)
stopifnot(isTRUE(validate_world_markets_contract(unavailable, "china_macro")))

mutations <- list(
  function(payload) {
    payload$china_macro$values_published <- TRUE
    payload
  },
  function(payload) {
    payload$china_macro$series_catalog[[1]]$latest_value <- "100.5"
    payload
  },
  function(payload) {
    payload$china_macro$series_catalog[[1]]$value <- "100.5"
    payload
  },
  function(payload) {
    payload$china_macro$series_catalog[[1]]$harmless_metric <- 100.5
    payload
  },
  function(payload) {
    payload$china_macro$knowledge_time <- NULL
    payload
  },
  function(payload) {
    payload$china_macro$provenance <- NULL
    payload
  },
  function(payload) {
    payload$china_macro$attestation <- NULL
    payload
  },
  function(payload) {
    payload$china_macro$provenance$raw_sha256 <- paste(rep("b", 64L), collapse = "")
    payload
  },
  function(payload) {
    payload$china_macro$provenance$raw_size_bytes <- 2048L
    payload
  },
  function(payload) {
    payload$china_macro$attestation$raw_sha256 <- paste(rep("b", 64L), collapse = "")
    payload
  },
  function(payload) {
    payload$china_macro$attestation$signed_at <- "2026-08-10T01:59:59Z"
    payload
  },
  function(payload) {
    payload$china_macro$knowledge_time <- "2026-08-10T02:00:00.000001Z"
    payload$china_macro$attestation$signed_at <- "2026-08-10T02:00:00Z"
    payload
  },
  function(payload) {
    payload$china_macro$series_catalog <- rev(payload$china_macro$series_catalog)
    payload
  },
  function(payload) {
    payload$clocks$domains$china_macro <- payload$china_macro$knowledge_time
    payload
  },
  function(payload) {
    payload$clocks$selected_evidence_as_of <- payload$china_macro$knowledge_time
    payload
  },
  function(payload) {
    payload$citation$evidence_as_of <- payload$china_macro$knowledge_time
    payload
  },
  function(payload) {
    payload$citation$evidence_as_of <- NULL
    payload
  },
  function(payload) {
    payload$clocks$selected_evidence_as_of <- NULL
    payload
  },
  function(payload) {
    payload$clocks$excluded_from_observation_clocks <- list()
    payload
  },
  function(payload) {
    clock <- payload$china_macro$knowledge_time
    payload$generated_at <- clock
    payload$clocks$snapshot_generated_at <- clock
    payload$citation$generated_at <- clock
    payload
  }
)
for (mutate in mutations) {
  expect_rejected(mutate(payload_for("china_macro")))
}

for (field in c("knowledge_time", "revision_id", "provenance", "attestation")) {
  payload <- payload_for("china_macro")
  payload$china_macro <- china_macro(FALSE)
  payload$china_macro[[field]] <- if (field %in% c("knowledge_time", "revision_id")) {
    "forged"
  } else {
    list()
  }
  expect_rejected(payload)
}

all_payload <- payload_for("all")
stopifnot(isTRUE(validate_world_markets_contract(all_payload, "all")))

missing_china <- payload_for("all")
missing_china$china_macro <- NULL
expect_rejected(missing_china)

missing_core <- payload_for("all")
missing_core$forex <- NULL
expect_rejected(missing_core)

named <- payload_for("forex")
named$china_macro <- china_macro()
expect_rejected(named, "forex")
