"""Supply Desk, the Wrightson-style forward cash table.

For each Treasury settlement date over the next four weeks: gross new
issuance, maturing amount, and NET NEW CASH in $B, split bills vs coupons.
Net new cash is the number desks actually trade off: it is the reserve
drain the calendar forces that day (issuance settles, TGA builds, reserves
fall) net of the cash the market gets back from maturing paper.

Three frames in, one table out. Announced rows come from the FiscalData
upcoming-auctions feed, with offering amounts where published and the last
comparable size where the amount is still TBA (flagged). Maturing amounts
come from realized auction results summed by maturity date, plus an MSPD
overlay for coupons issued before the auction-history window: a 2016 10y
or a 1996 30y maturing on the mid-month refunding date is invisible to a
feed that starts in 2018, and that is real money. Beyond the announced
horizon each tenor's last size is carried forward at its own observed
cadence so the table always covers the full four weeks; those rows carry
projected=true so the house projection can be graded later.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

PROJ_LOOKBACK_D = 180     # issue history window for cadence estimation
PROJ_MIN_ISSUES = 4       # issues needed before a tenor earns a projection
PROJ_MAX_GAP_D = 35       # weekly bills and monthly coupons; quarterly originals excluded
MSPD_COLS = ("maturity_date", "issue_date", "outstanding_amt")  # overlay needs all three


def _num(col: pd.Series) -> pd.Series:
    return pd.to_numeric(col.astype(str).str.replace(",", ""), errors="coerce")


def _is_bill(security_type: pd.Series) -> pd.Series:
    return security_type.astype(str).str.contains("Bill", case=False, na=False)


_TERM_RE = re.compile(r"(\d+)\s*-?\s*(Year|Month|Week|Day)", re.IGNORECASE)


def _tenor_key(security_type: pd.Series, security_term: pd.Series) -> pd.Series:
    """Group by ORIGINAL tenor, not by the term Treasury prints.

    FiscalData states security_term as the REMAINING term on a reopening, so a
    single economic tenor arrives as "Note 10-Year", "Note 9-Year 11-Month",
    "Note 9-Year 10-Month" and so on. Keying on the raw string fragmented every
    reopened coupon into groups too small to clear the projection threshold, so
    10y, 20y and 30y issuance was never projected while their maturities were
    counted in full: the table printed phantom deep-negative net-new-cash rows
    on refunding dates. Rounding UP to the nearest standard tenor rejoins the
    reopenings with their original, which is the unit Treasury actually issues.
    """
    STANDARD_Y = [2, 3, 5, 7, 10, 20, 30]

    def one(t: str, term: str) -> str:
        t = str(t).strip()
        term = str(term).strip()
        months = 0.0
        for n, unit in _TERM_RE.findall(term):
            u = unit.lower()
            months += float(n) * (12.0 if u == "year" else 1.0 if u == "month"
                                  else 12.0 / 52.0 if u == "week" else 12.0 / 365.0)
        if months <= 0:
            return f"{t} {term}"
        if "bill" in t.lower():
            # Bills are not reopened into odd remaining terms often enough to
            # need rounding, and their weeks matter; keep them as stated.
            return f"{t} {term}"
        years = months / 12.0
        for s in STANDARD_Y:
            if years <= s + 0.05:
                return f"{t} {s}-Year"
        return f"{t} {term}"

    return pd.Series([one(a, b) for a, b in zip(security_type, security_term)],
                     index=security_type.index)


def _roll_to_bday(d: pd.Timestamp) -> pd.Timestamp:
    while d.dayofweek >= 5:
        d = d + pd.Timedelta(days=1)
    return d


def forward_table(
    upcoming: pd.DataFrame,            # FiscalData od/upcoming_auctions rows
    auctions: pd.DataFrame,            # FiscalData od/auctions_query results
    mspd: pd.DataFrame | None = None,  # MSPD table 3 marketable detail (pre-window coupons)
    *,
    today: pd.Timestamp | None = None,
    horizon_days: int = 28,
    auctions_start: str = "2018-01-01",
) -> dict:
    if auctions is None or auctions.empty:
        return {"ok": False, "reason": "no auction history"}
    for col in ("security_type", "security_term", "issue_date", "maturity_date", "total_accepted"):
        if col not in auctions.columns:
            return {"ok": False, "reason": f"auction history missing {col}"}

    today = pd.Timestamp.now().normalize() if today is None else pd.Timestamp(today).normalize()
    end = today + pd.Timedelta(days=horizon_days)

    au = auctions.copy()
    au["issue_date"] = pd.to_datetime(au["issue_date"], errors="coerce")
    au["maturity_date"] = pd.to_datetime(au["maturity_date"], errors="coerce")
    au["accepted_b"] = _num(au["total_accepted"]) / 1e9
    if "offering_amt" in au.columns:
        au["offering_b"] = _num(au["offering_amt"]) / 1e9
    else:
        au["offering_b"] = np.nan
    au["bill"] = _is_bill(au["security_type"])
    au["term"] = _tenor_key(au["security_type"], au["security_term"])
    au = au.dropna(subset=["issue_date"]).sort_values("issue_date")
    # History means settled reality: rows at or beyond today belong to the
    # announced frame, not to size/cadence estimation.
    hist = au[au["issue_date"] < today]

    # Last realized size per tenor: fills TBA announcements and prices the
    # projection rows. Prefer what was actually accepted over what was offered.
    last_size: dict[str, float] = {}
    for term, g in hist.groupby("term"):
        s = g["accepted_b"].dropna()
        if s.empty:
            s = g["offering_b"].dropna()
        if not s.empty:
            last_size[term] = float(s.iloc[-1])

    # ---- announced frame ---------------------------------------------------
    issuance: list[dict] = []
    announced_through = None
    up = upcoming if upcoming is not None else pd.DataFrame()
    if not up.empty and {"issue_date", "security_type", "security_term"}.issubset(up.columns):
        u = up.copy()
        u["issue_date"] = pd.to_datetime(u["issue_date"], errors="coerce")
        # Stale-vintage guard: the feed has shipped 2024 rows before; nothing
        # settling in the past is upcoming.
        u = u.dropna(subset=["issue_date"])
        u = u[(u["issue_date"] >= today) & (u["issue_date"] <= end)]
        u["bill"] = _is_bill(u["security_type"])
        u["term"] = _tenor_key(u["security_type"], u["security_term"])
        u["amt_b"] = _num(u["offering_amt"]) / 1e9 if "offering_amt" in u.columns else np.nan
        for _, row in u.iterrows():
            amt = row["amt_b"]
            estimated = False
            if pd.isna(amt):
                amt = last_size.get(row["term"])
                estimated = amt is not None
            if amt is None or pd.isna(amt):
                continue  # announced, no amount, no comparable: nothing honest to book
            issuance.append(
                {
                    "date": row["issue_date"],
                    "term": row["term"],
                    "bill": bool(row["bill"]),
                    "amt_b": float(amt),
                    "projected": False,
                    "estimated": bool(estimated),
                }
            )
    # Auctioned but not yet settled: Treasury drops rows from the upcoming
    # feed at auction time, days before they settle, so without the results
    # overlay the gross side goes dark between auction and issue (observed
    # live 2026-07-28: Monday's 13/26-week bills settling Thursday were in
    # neither feed's future rows). Results are final; they supersede their
    # own announcement.
    pending = au[(au["issue_date"] >= today) & (au["issue_date"] <= end)].dropna(subset=["accepted_b"])
    if not pending.empty:
        supersede = set(zip(pending["term"], pending["issue_date"]))
        issuance = [r for r in issuance if (r["term"], r["date"]) not in supersede]
        for _, row in pending.iterrows():
            issuance.append(
                {
                    "date": row["issue_date"],
                    "term": row["term"],
                    "bill": bool(row["bill"]),
                    "amt_b": float(row["accepted_b"]),
                    "projected": False,
                    "estimated": False,
                }
            )

    if issuance:
        announced_through = max(r["date"] for r in issuance)
        # The freshest announced or accepted size beats the last realized one
        # as the carry-forward for projection rows (Treasury moves bill sizes
        # in $5-10B steps and the announcement is the newest information).
        for r in sorted((r for r in issuance if not r["estimated"]), key=lambda r: r["date"]):
            last_size[r["term"]] = r["amt_b"]

    # ---- house projection beyond the announced horizon ----------------------
    # Per tenor: last observed issue (realized or announced), median gap over
    # the recent issues, last size carried forward. Only tenors with a regular
    # cadence inside PROJ_MAX_GAP_D earn rows; quarterly refunding originals
    # and one-off CMBs stay out rather than get guessed at.
    announced_dates: dict[str, list[pd.Timestamp]] = {}
    for r in issuance:
        announced_dates.setdefault(r["term"], []).append(r["date"])
    recent = hist[hist["issue_date"] >= today - pd.Timedelta(days=PROJ_LOOKBACK_D)]
    for term, g in recent.groupby("term"):
        dates = sorted(set(g["issue_date"].tolist()) | set(announced_dates.get(term, [])))
        if len(dates) < PROJ_MIN_ISSUES or term not in last_size:
            continue
        gaps = np.diff([d.value for d in dates[-8:]]) / 86_400_000_000_000  # ns -> days
        cadence = float(np.median(gaps))
        if not (4.0 <= cadence <= PROJ_MAX_GAP_D):
            continue
        is_bill = bool(g["bill"].iloc[-1])
        anchor = dates[-1]
        k = 1
        while True:
            d = anchor + pd.Timedelta(days=round(cadence * k))
            if d > end:
                break
            k += 1
            if d < today:
                continue
            d_settle = _roll_to_bday(d)
            if d_settle in announced_dates.get(term, []):
                continue  # already booked from the announcement
            issuance.append(
                {
                    "date": d_settle,
                    "term": term,
                    "bill": is_bill,
                    "amt_b": last_size[term],
                    "projected": True,
                    "estimated": False,
                }
            )

    if not issuance:
        return {"ok": False, "reason": "no announced or projectable issuance inside the horizon"}

    # ---- maturing side -------------------------------------------------------
    # Face maturing = every auction's accepted amount summed by maturity date
    # (originals and reopenings each contributed their own accepted face).
    mat = au.dropna(subset=["maturity_date", "accepted_b"]).copy()
    # Weekend-dated maturities pay the next business day (Aug 15 2026 is a
    # Saturday: the cash lands Monday). Roll before the window filter.
    mat["maturity_date"] = mat["maturity_date"].map(_roll_to_bday)
    mat = mat[(mat["maturity_date"] >= today) & (mat["maturity_date"] <= end)]
    mat_bill = mat[mat["bill"]].groupby("maturity_date")["accepted_b"].sum()
    mat_cpn = mat[~mat["bill"]].groupby("maturity_date")["accepted_b"].sum()

    # A renamed or dropped MSPD column degrades to no overlay, named in the
    # caveats, the same as an absent frame. It never takes the table down.
    mspd_used = False
    mspd_missing: list[str] = []
    if mspd is not None and not mspd.empty:
        mspd_missing = [c for c in MSPD_COLS if c not in mspd.columns]
        if not mspd_missing:
            m = mspd.copy()
            m["maturity_date"] = pd.to_datetime(m["maturity_date"], errors="coerce")
            m["issue_date"] = pd.to_datetime(m["issue_date"], errors="coerce")
            m["out_b"] = _num(m["outstanding_amt"]) / 1e3  # $M -> $B, null on continuation rows
            m = m.dropna(subset=["maturity_date", "issue_date", "out_b"])
            m["maturity_date"] = m["maturity_date"].map(_roll_to_bday)
            # Only pre-window issues: everything since auctions_start is already
            # counted from auction results, and MSPD is too stale for fresh bills.
            m = m[
                (m["issue_date"] < pd.Timestamp(auctions_start))
                & (m["maturity_date"] >= today)
                & (m["maturity_date"] <= end)
            ]
            if not m.empty:
                old_cpn = m.groupby("maturity_date")["out_b"].sum()
                mat_cpn = mat_cpn.add(old_cpn, fill_value=0.0)
                mspd_used = True

    # ---- the table -----------------------------------------------------------
    iss = pd.DataFrame(issuance)
    gross_bill = iss[iss["bill"]].groupby("date")["amt_b"].sum()
    gross_cpn = iss[~iss["bill"]].groupby("date")["amt_b"].sum()
    proj_dates = set(iss[iss["projected"]]["date"])
    est_dates = set(iss[iss["estimated"]]["date"])

    all_dates = sorted(
        set(gross_bill.index) | set(gross_cpn.index) | set(mat_bill.index) | set(mat_cpn.index)
    )
    rows = []
    for d in all_dates:
        gb = float(gross_bill.get(d, 0.0))
        gc = float(gross_cpn.get(d, 0.0))
        mb = float(mat_bill.get(d, 0.0))
        mc = float(mat_cpn.get(d, 0.0))
        rows.append(
            {
                "date": d.date().isoformat(),
                "bills_gross_b": round(gb, 1),
                "coupons_gross_b": round(gc, 1),
                "gross_b": round(gb + gc, 1),
                "bills_maturing_b": round(mb, 1),
                "coupons_maturing_b": round(mc, 1),
                "maturing_b": round(mb + mc, 1),
                "bills_net_b": round(gb - mb, 1),
                "coupons_net_b": round(gc - mc, 1),
                "net_new_cash_b": round((gb + gc) - (mb + mc), 1),
                "projected": d in proj_dates,
                "amount_estimated": d in est_dates,
                # Quarterly refunding originals are deliberately not projected
                # (no cadence under 5 weeks), but their maturities are counted
                # in full. Past the announced horizon that asymmetry prints a
                # deep negative for a date Treasury will in fact fund, so the
                # row says its net is not usable rather than implying a drain
                # that is an artifact of what we decline to guess.
                "issuance_incomplete": bool(
                    announced_through is not None
                    and d > announced_through
                    and (mb + mc) > 0
                    and (gb + gc) < 0.5 * (mb + mc)
                ),
            }
        )

    total_gross = sum(r["gross_b"] for r in rows)
    total_mat = sum(r["maturing_b"] for r in rows)
    heaviest = max(rows, key=lambda r: r["net_new_cash_b"])

    caveats = [
        "rows marked issuance_incomplete sit past the announced horizon on a date whose maturities "
        "are known but whose refunding issuance is not projected (quarterly originals have no short "
        "cadence to extrapolate); their net is an artifact, not a forecast, and should not be read as a drain",
        "maturing includes Fed SOMA holdings, which roll over at auction; net new cash to private investors is smaller on SOMA-heavy dates",
        "Treasury buyback retirements are not netted from the maturing stock",
        "TBA offering amounts are filled with the tenor's last realized size (amount_estimated=true)",
        "projected rows carry each tenor's last size forward at its observed cadence; tenors without a regular cadence under 5 weeks (quarterly refunding originals, one-off CMBs) are not projected",
    ]
    if not mspd_used:
        why = f" (frame is missing {', '.join(mspd_missing)})" if mspd_missing else ""
        caveats.append(
            f"no MSPD overlay{why}: coupons issued before {auctions_start} are missing, so maturing is understated on mid-month refunding dates"
        )

    return {
        "ok": True,
        "asof": today.date().isoformat(),
        "horizon_end": end.date().isoformat(),
        "announced_through": announced_through.date().isoformat() if announced_through is not None else None,
        "rows": rows,
        "totals": {
            "gross_b": round(total_gross, 1),
            "maturing_b": round(total_mat, 1),
            "net_new_cash_b": round(total_gross - total_mat, 1),
            "bills_net_b": round(sum(r["bills_net_b"] for r in rows), 1),
            "coupons_net_b": round(sum(r["coupons_net_b"] for r in rows), 1),
        },
        "heaviest_day": {"date": heaviest["date"], "net_new_cash_b": heaviest["net_new_cash_b"]},
        "method": "announced offerings by settlement date net of maturing (auction results summed by maturity date + MSPD overlay for pre-window coupons); last-size-at-cadence house projection past the announced horizon",
        "caveats": caveats,
    }
