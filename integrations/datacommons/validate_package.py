#!/usr/bin/env python3
"""Offline structural checks for the Data Commons readiness package."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

from build_observations import ENTITY, ROOT, SERIES


EXPECTED_FIELDS = {
    "entity",
    "variable",
    "date",
    "value",
    "unit",
    "measurementMethod",
    "observationPeriod",
}

EXPECTED_QUALIFIERS = {
    "seiche/OfrDvpServiceTotalRepo",
    "seiche/OfrTriPartyTotalRepo",
    "seiche/OfrDvpServiceOvernightOpenRepo",
    "seiche/OfrTriPartyOvernightOpenRepo",
    "seiche/OfrGcfServiceOvernightOpenRepo",
    "seiche/OfrMoneyMarketFundAllSecurities",
    "seiche/OfrMoneyMarketFundRepoClearedByFicc",
    "seiche/OfrMoneyMarketFundRepoWithFederalReserve",
    "seiche/OfrMoneyMarketFundAllRepo",
}


def validate() -> None:
    input_dir = ROOT / "input"
    config = json.loads((input_dir / "config.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "generated-manifest.json").read_text(encoding="utf-8"))
    eligibility = list(
        csv.DictReader((ROOT / "eligibility.csv").open(encoding="utf-8", newline=""))
    )

    assert config["includeInputSubdirs"] is True
    assert config["groupStatVarsByProperty"] is False
    assert len(SERIES) == 10
    assert sum(row["status"] == "candidate_eligible_for_review" for row in eligibility) == 2
    assert any(row["status"] == "blocked_fred_terms" for row in eligibility)
    assert any(row["status"] == "blocked_semantic_mismatch" for row in eligibility)

    mcf = (input_dir / "stat_vars.mcf").read_text(encoding="utf-8")
    for spec in SERIES:
        assert f"Node: dcid:{spec.variable}" in mcf
    assert 'measurementQualifier: "' not in mcf
    qualifier_nodes = set(
        re.findall(r"measurementQualifier: dcid:([^\s]+)", mcf)
    )
    assert qualifier_nodes == EXPECTED_QUALIFIERS
    for qualifier in EXPECTED_QUALIFIERS:
        assert f"Node: dcid:{qualifier}\ntypeOf: dcid:MeasurementQualifierEnum" in mcf

    expected_variables = {spec.variable for spec in SERIES}
    expected_files = {spec.output_file for spec in SERIES}
    seen_variables: set[str] = set()
    seen_keys: set[tuple[str, str, str]] = set()

    outputs = {Path(item["path"]).name: item for item in manifest["outputs"]}
    assert set(outputs) == expected_files
    for filename in expected_files:
        path = input_dir / "observations" / filename
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            assert set(reader.fieldnames or ()) == EXPECTED_FIELDS
            row_count = 0
            for row in reader:
                row_count += 1
                assert row["entity"] == ENTITY
                assert row["variable"] in expected_variables
                datetime.strptime(row["date"], "%Y-%m-%d")
                assert math.isfinite(float(row["value"]))
                assert row["unit"] in {"Percent", "USDollar"}
                assert row["measurementMethod"] in {
                    "seiche/OfrPreliminaryRelease",
                    "seiche/OfrMonthlyRevisedSeries",
                }
                assert row["observationPeriod"] == "P1D"
                key = (row["entity"], row["variable"], row["date"])
                assert key not in seen_keys
                seen_keys.add(key)
                seen_variables.add(row["variable"])
            assert row_count == outputs[filename]["row_count"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == outputs[filename]["sha256"]

    assert seen_variables == expected_variables
    assert manifest["schema"] == "seiche.datacommons-build-manifest.v1"
    assert manifest["entity"] == ENTITY
    assert len(manifest["series"]) == len(SERIES)


if __name__ == "__main__":
    validate()
    print("Data Commons readiness package is structurally valid.")
