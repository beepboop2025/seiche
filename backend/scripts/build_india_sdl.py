"""Build seiche/india_sdl_auctions.json from RBI's 'State Government Securities - Full Auction Result' releases.

Evidence (outside Git): raw/pr_<prid>.html fetched by the identified LiquiLens research client with receipts
(raw/receipts.json), parsed by work/parse_sdl.py into work/sdl_rows.json (one row per security per auction, every
value with the cell it was printed in) and work/sdl_report.json (per release: dates and the check that the securities
add up to the release's printed Total column). Nothing is typed by hand except the state-name map in parse_sdl.py.

Usage: python3 scripts/build_india_sdl.py [EVIDENCE_DIR]
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

DEFAULT_EVIDENCE = Path.home() / "FastWorkspace/evidence/smallmid-fi-20261111-wave2/sdl-auctions"
OUT = Path(__file__).resolve().parents[1] / "seiche" / "india_sdl_auctions.json"
SCHEMA = "seiche.india-sdl-auctions.v1"
COLUMNS = ("auction_date", "prid", "state", "state_as_printed", "security", "maturity_year", "tenor_as_printed",
           "notified_cr", "competitive_bids_cr", "competitive_bids_no", "cutoff_yield_pct", "cutoff_price_rs",
           "weighted_avg_yield_pct", "allotted_cr", "noncompetitive_allotted_cr", "devolvement_cr")


def _date(text: str | None) -> str | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def build(evidence: Path) -> dict:
    rows = json.loads((evidence / "work" / "sdl_rows.json").read_text())
    report = json.loads((evidence / "work" / "sdl_report.json").read_text())
    receipts = json.loads((evidence / "raw" / "receipts.json").read_text())["sources"]
    releases = {}
    for name, check in sorted(report.items()):
        rec = receipts[name]
        prid = int(name[3:-5])
        held = _date(check.get("auction_held_on_printed")) or _date(check.get("date_printed"))
        releases[str(prid)] = {"release_date": _date(check.get("date_printed")), "auction_date": held,
                               "url": rec["requested_url"], "retrieved_utc": rec["retrieved_utc"],
                               "sha256": rec["sha256"],
                               "total_check": ("matched" if all(v for k, v in check.items() if k.startswith("sum_ok"))
                                               and any(k.startswith("sum_ok") for k in check) else "no_printed_total"),
                               "unparsed_labels": len(check.get("problems", []))}
    out_rows = []
    for r in rows:
        rel = releases[str(r["prid"])]
        out_rows.append([rel["auction_date"], r["prid"], r.get("state"), r.get("state_as_printed"),
                         r["security_as_printed"].replace("–", "-"), r.get("maturity_year"),
                         r.get("tenor_as_printed"), r.get("notified_amount_cr"), r.get("comp_bids_received_cr"),
                         r.get("comp_bids_received_no"), r.get("cutoff_yield_pct"), r.get("cutoff_price_rs"),
                         r.get("weighted_avg_yield_pct"), r.get("total_allotment_cr"),
                         r.get("noncomp_bids_accepted_cr"), r.get("devolvement_on_pds_cr")])
    out_rows.sort(key=lambda x: (x[0] or "", x[2] or "", x[4]))
    checked = sum(1 for r in releases.values() if r["total_check"] == "matched")
    return {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": {"publisher": "Reserve Bank of India",
                   "title": "State Government Securities: Full Auction Result (press releases)",
                   "rights": "RBI auction results, attributed to RBI (Seiche adapter rbi_auctions, official open).",
                   "releases": len(releases), "first_auction": min(r["auction_date"] for r in releases.values()),
                   "last_auction": max(r["auction_date"] for r in releases.values()),
                   "releases_with_total_matched": checked,
                   "method": "Each release read from its HTML tables; every security's notified amount and allotment "
                             "must add up to the release's printed Total where one is printed (no release failed). A "
                             "printed '-' or 0 under the cut-off means no bid was accepted and stays empty. States are "
                             "kept as printed and also mapped to a canonical name by a stated list."},
        "columns": list(COLUMNS),
        "rows": out_rows,
        "releases": releases,
    }


def main() -> int:
    evidence = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_EVIDENCE
    pack = build(evidence)
    OUT.write_text(json.dumps(pack, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"{len(pack['rows'])} rows, {pack['source']['releases']} releases, {OUT.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
