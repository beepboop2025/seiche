#!/usr/bin/env python3
"""Build the Data Commons candidate observations from the official OFR API.

This importer intentionally does not read Seiche's local database or public CSV
mirror. OFR volume observations are stored there as raw dollars while the
Seiche display layer scales them to billions, and several other Seiche sources
carry terms that are not suitable for an AI-oriented public data commons.

Only the ten OFR-produced series in ``SERIES`` are admitted. Any source,
release, unit, frequency, vintage, or rights change fails closed before an
observation file is replaced.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://data.financialresearch.gov/v1/series/full"
USER_AGENT = "Seiche-Data-Commons-Readiness/0.1 (+https://seiche.info/)"
ENTITY = "country/USA"
ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
OBSERVATION_DIR = INPUT_DIR / "observations"


@dataclass(frozen=True)
class SeriesSpec:
    seiche_mnemonic: str
    upstream_mnemonic: str
    variable: str
    release: str
    release_slug: str
    name: str
    unit: str
    frequency: str
    vintage: str
    measurement_method: str
    output_file: str


SERIES = (
    SeriesSpec(
        "DVP_VOL",
        "REPO-DVP_TV_TOT-P",
        "seiche/OfrDvpRepoTotal_Sum_Amount_FinancialTransaction",
        "OFR U.S. Repo Markets Data Release",
        "repo",
        "DVP Service Transaction Volume: Total (Preliminary)",
        "USD",
        "Daily",
        "Preliminary",
        "seiche/OfrPreliminaryRelease",
        "ofr_repo_markets.csv",
    ),
    SeriesSpec(
        "TRI_VOL",
        "REPO-TRI_TV_TOT-P",
        "seiche/OfrTriPartyRepoTotal_Sum_Amount_FinancialTransaction",
        "OFR U.S. Repo Markets Data Release",
        "repo",
        "Tri-Party Transaction Volume: Total (Preliminary)",
        "USD",
        "Daily",
        "Preliminary",
        "seiche/OfrPreliminaryRelease",
        "ofr_repo_markets.csv",
    ),
    SeriesSpec(
        "DVP_RATE_OO",
        "REPO-DVP_AR_OO-P",
        "seiche/OfrDvpOvernightOpenRepo_Mean_InterestRate_FinancialInstrument",
        "OFR U.S. Repo Markets Data Release",
        "repo",
        "DVP Service Average Rate: Overnight/Open (Preliminary)",
        "Percent",
        "Daily",
        "Preliminary",
        "seiche/OfrPreliminaryRelease",
        "ofr_repo_markets.csv",
    ),
    SeriesSpec(
        "TRI_RATE_OO",
        "REPO-TRI_AR_OO-P",
        "seiche/OfrTriPartyOvernightOpenRepo_Mean_InterestRate_FinancialInstrument",
        "OFR U.S. Repo Markets Data Release",
        "repo",
        "Tri-Party Average Rate: Overnight/Open (Preliminary)",
        "Percent",
        "Daily",
        "Preliminary",
        "seiche/OfrPreliminaryRelease",
        "ofr_repo_markets.csv",
    ),
    SeriesSpec(
        "GCF_RATE_OO",
        "REPO-GCF_AR_OO-P",
        "seiche/OfrGcfOvernightOpenRepo_Mean_InterestRate_FinancialInstrument",
        "OFR U.S. Repo Markets Data Release",
        "repo",
        "GCF Repo Service Average Rate: Overnight/Open (Preliminary)",
        "Percent",
        "Daily",
        "Preliminary",
        "seiche/OfrPreliminaryRelease",
        "ofr_repo_markets.csv",
    ),
    SeriesSpec(
        "GCF_VOL_OO",
        "REPO-GCF_TV_OO-P",
        "seiche/OfrGcfOvernightOpenRepo_Sum_Amount_FinancialTransaction",
        "OFR U.S. Repo Markets Data Release",
        "repo",
        "GCF Repo Service Transaction Volume: Overnight/Open (Preliminary)",
        "USD",
        "Daily",
        "Preliminary",
        "seiche/OfrPreliminaryRelease",
        "ofr_repo_markets.csv",
    ),
    SeriesSpec(
        "MMF_TOT",
        "MMF-MMF_TOT-M",
        "seiche/OfrMoneyMarketFundTotal_Sum_Amount_Investment",
        "OFR U.S. Money Market Fund Data Release",
        "mmf",
        "Money Market Mutual Fund Investments: Total",
        "USD",
        "Monthly",
        "Monthly Revisions - Complete Series",
        "seiche/OfrMonthlyRevisedSeries",
        "ofr_money_market_funds.csv",
    ),
    SeriesSpec(
        "MMF_REPO_FICC",
        "MMF-MMF_RP_wFICC-M",
        "seiche/OfrMoneyMarketFundRepoFicc_Sum_Amount_Investment",
        "OFR U.S. Money Market Fund Data Release",
        "mmf",
        "Money Market Mutual Fund Investments in Repurchase Agreements Cleared by FICC",
        "USD",
        "Monthly",
        "Monthly Revisions - Complete Series",
        "seiche/OfrMonthlyRevisedSeries",
        "ofr_money_market_funds.csv",
    ),
    SeriesSpec(
        "MMF_REPO_FED",
        "MMF-MMF_RP_wFR-M",
        "seiche/OfrMoneyMarketFundRepoFederalReserve_Sum_Amount_Investment",
        "OFR U.S. Money Market Fund Data Release",
        "mmf",
        "Money Market Mutual Fund Investments in Repurchase Agreements with the Federal Reserve",
        "USD",
        "Monthly",
        "Monthly Revisions - Complete Series",
        "seiche/OfrMonthlyRevisedSeries",
        "ofr_money_market_funds.csv",
    ),
    SeriesSpec(
        "MMF_REPO_TOT",
        "MMF-MMF_RP_TOT-M",
        "seiche/OfrMoneyMarketFundRepoTotal_Sum_Amount_Investment",
        "OFR U.S. Money Market Fund Data Release",
        "mmf",
        "Money Market Mutual Fund Investments in Repurchase Agreements",
        "USD",
        "Monthly",
        "Monthly Revisions - Complete Series",
        "seiche/OfrMonthlyRevisedSeries",
        "ofr_money_market_funds.csv",
    ),
)


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"OFR returned HTTP {response.status} for {url}")
        return json.load(response)


def _series_url(spec: SeriesSpec, start_date: str) -> str:
    return BASE_URL + "?" + urllib.parse.urlencode(
        {
            "mnemonic": spec.upstream_mnemonic,
            "start_date": start_date,
            "remove_nulls": "true",
            "time_format": "date",
        }
    )


def _validate_metadata(spec: SeriesSpec, payload: dict[str, Any]) -> None:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"{spec.upstream_mnemonic}: missing metadata")
    checks = {
        "mnemonic": (metadata.get("mnemonic"), spec.upstream_mnemonic),
        "release": (
            (metadata.get("release") or {}).get("long_name"),
            spec.release,
        ),
        "release slug": (
            (metadata.get("release") or {}).get("href"),
            f"/short-term-funding-monitor/datasets/{spec.release_slug}/",
        ),
        "name": ((metadata.get("description") or {}).get("name"), spec.name),
        "unit": ((metadata.get("unit") or {}).get("name"), spec.unit),
        "frequency": (
            (metadata.get("schedule") or {}).get("observation_frequency"),
            spec.frequency,
        ),
        "observation period": (
            (metadata.get("schedule") or {}).get("observation_period"),
            "Single Day",
        ),
        "vintage": (
            (metadata.get("description") or {}).get("vintage"),
            spec.vintage,
        ),
    }
    for label, (actual, expected) in checks.items():
        if actual != expected:
            raise ValueError(
                f"{spec.upstream_mnemonic}: {label} changed: "
                f"expected {expected!r}, got {actual!r}"
            )

    rights = str((metadata.get("rights") or {}).get("description") or "").strip()
    if rights:
        raise ValueError(
            f"{spec.upstream_mnemonic}: upstream added a rights condition: {rights}"
        )


def _fetch(spec: SeriesSpec, start_date: str) -> tuple[list[list[Any]], str, str]:
    url = _series_url(spec, start_date)
    raw = _request_json(url)
    payload = raw.get(spec.upstream_mnemonic)
    if not isinstance(payload, dict):
        raise ValueError(f"{spec.upstream_mnemonic}: missing top-level series object")
    _validate_metadata(spec, payload)
    rows = (payload.get("timeseries") or {}).get("aggregation")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{spec.upstream_mnemonic}: no observations")

    normalized: list[list[Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError(f"{spec.upstream_mnemonic}: malformed row {index}")
        date, value = row
        datetime.strptime(str(date), "%Y-%m-%d")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{spec.upstream_mnemonic}: non-finite value on {date}")
        normalized.append([str(date), number])
    normalized.sort(key=lambda item: item[0])
    if len({item[0] for item in normalized}) != len(normalized):
        raise ValueError(f"{spec.upstream_mnemonic}: duplicate observation dates")
    return normalized, url, (payload.get("metadata") or {}).get("schedule", {}).get(
        "last_update", ""
    )


def _plain_number(value: float) -> str:
    return format(value, ".15g")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> str:
    fields = (
        "entity",
        "variable",
        "date",
        "value",
        "unit",
        "measurementMethod",
        "observationPeriod",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(start_date: str) -> dict[str, Any]:
    datetime.strptime(start_date, "%Y-%m-%d")
    rows_by_file: dict[str, list[dict[str, str]]] = {}
    manifest_series: list[dict[str, Any]] = []

    for spec in SERIES:
        observations, source_url, source_updated = _fetch(spec, start_date)
        rows = rows_by_file.setdefault(spec.output_file, [])
        for date, value in observations:
            rows.append(
                {
                    "entity": ENTITY,
                    "variable": spec.variable,
                    "date": date,
                    "value": _plain_number(value),
                    "unit": "USDollar" if spec.unit == "USD" else "Percent",
                    "measurementMethod": spec.measurement_method,
                    "observationPeriod": "P1D",
                }
            )
        manifest_series.append(
            {
                **asdict(spec),
                "source_url": source_url,
                "source_last_update": source_updated,
                "observation_count": len(observations),
                "first_observation": observations[0][0],
                "last_observation": observations[-1][0],
            }
        )

    outputs: list[dict[str, Any]] = []
    for filename, rows in sorted(rows_by_file.items()):
        rows.sort(key=lambda item: (item["variable"], item["date"]))
        path = OBSERVATION_DIR / filename
        outputs.append(
            {
                "path": str(path.relative_to(ROOT)),
                "row_count": len(rows),
                "sha256": _write_csv(path, rows),
            }
        )

    manifest = {
        "schema": "seiche.datacommons-build-manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entity": ENTITY,
        "source_policy": (
            "Direct OFR API only; each admitted series must have an empty OFR "
            "metadata.rights.description field and pinned OFR release metadata."
        ),
        "series": manifest_series,
        "outputs": outputs,
    }
    manifest_path = ROOT / "generated-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2010-01-01")
    args = parser.parse_args()
    manifest = build(args.start_date)
    for output in manifest["outputs"]:
        print(f"{output['path']}: {output['row_count']} rows ({output['sha256']})")


if __name__ == "__main__":
    main()
