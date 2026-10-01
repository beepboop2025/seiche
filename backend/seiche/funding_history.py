"""Bounded, rights-filtered histories for the public charts' dated fallback."""
from seiche import methodology, store

FUNDING_KEYS = (
    "SOFR", "EFFR", "IORB", "TGCR", "BGCR", "WRESBAL", "TGA_LONG",
    "RRPONTSYD", "WALCL", "CP_FIN_3M", "CP_NONFIN_3M", "DGS3M",
    "DGS2", "DGS10", "DGS30", "MMF_REPO_FED", "MMF_REPO_FICC", "MMF_REPO_TOT",
)


def public_histories(generated_at: str) -> dict:
    rows = []
    for entry in methodology.series_index()["series"]:
        key = entry["mnemonic"]
        if (key not in FUNDING_KEYS or not entry["available"]
                or entry["csv_restricted"] or methodology.csv_restriction(key)
                or entry["json"] != f"/api/series/{key}"):
            continue
        series = store.load_series(key)
        if series is not None:
            rows.append({"entry": entry, "payload": {
                "provenance": series.provenance(), "points": series.tail_records(520),
            }})
    return {"schema": "seiche.funding-history.v1", "generated_at": generated_at, "series": rows}
