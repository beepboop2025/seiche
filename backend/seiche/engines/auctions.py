"""Auction Digestion Index — is the market choking on Treasury supply?

Each auction is scored against the trailing distribution of its own tenor:
weak bid-to-cover, heavy primary-dealer takedown (buyers of last resort),
soft indirects (foreign demand). EWMA across auctions in time order gives a
cumulative "indigestion" gauge; March 2026's tailed 2s/5s/7s is the reference
signature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_HISTORY = 8  # same-tenor auctions needed before scoring


def analyze(auctions: pd.DataFrame) -> dict:
    if auctions.empty:
        return {"ok": False, "reason": "no auction data"}

    df = auctions.copy()
    df["auction_date"] = pd.to_datetime(df["auction_date"])
    for col in ("bid_to_cover_ratio", "primary_dealer_accepted", "indirect_bidder_accepted", "total_accepted"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["bid_to_cover_ratio", "total_accepted"])
    df = df[df["total_accepted"] > 0].sort_values("auction_date")
    df["pd_share"] = df["primary_dealer_accepted"] / df["total_accepted"]
    df["indirect_share"] = df["indirect_bidder_accepted"] / df["total_accepted"]
    df["tenor"] = df["security_type"].str.strip() + " " + df["security_term"].str.strip()

    scored = []
    for tenor, grp in df.groupby("tenor"):
        g = grp.sort_values("auction_date").reset_index(drop=True)
        for i in range(MIN_HISTORY, len(g)):
            hist, row = g.iloc[:i], g.iloc[i]
            def z(col, val):
                sd = hist[col].std()
                return float((val - hist[col].mean()) / sd) if sd and sd > 0 else 0.0
            btc_z = z("bid_to_cover_ratio", row["bid_to_cover_ratio"])
            pd_z = z("pd_share", row["pd_share"])
            ind_z = z("indirect_share", row["indirect_share"])
            score = 0.5 * (-btc_z) + 0.3 * pd_z + 0.2 * (-ind_z)  # + = poorly digested
            scored.append(
                {
                    "date": row["auction_date"],
                    "tenor": tenor,
                    "security_type": row["security_type"],
                    "btc": round(float(row["bid_to_cover_ratio"]), 2),
                    "btc_z": round(btc_z, 2),
                    "pd_share": round(float(row["pd_share"]), 3),
                    "pd_share_z": round(pd_z, 2),
                    "indirect_z": round(ind_z, 2),
                    "score": round(float(score), 2),
                }
            )
    if not scored:
        return {"ok": False, "reason": "insufficient same-tenor history"}

    s = pd.DataFrame(scored).sort_values("date")
    # Coupons carry the duration-absorption signal; weight bills less.
    s["w"] = np.where(s["security_type"].str.contains("Bill", case=False), 0.35, 1.0)
    s["wscore"] = s["score"] * s["w"]
    index = s.set_index("date")["wscore"].ewm(span=20).mean()

    recent = s.tail(15)[["date", "tenor", "btc", "btc_z", "pd_share", "pd_share_z", "score"]]
    recent = recent.assign(date=recent["date"].dt.date.astype(str))
    return {
        "_index_full": index,  # pd.Series for the history layer; stripped from payloads
        "ok": True,
        "asof": s["date"].iloc[-1].date().isoformat(),
        "digestion_index": round(float(index.iloc[-1]), 2),
        "recent_auctions": recent.to_dict("records"),
        "index_series": [
            [d.date().isoformat(), round(float(v), 2)] for d, v in index.tail(300).items()
        ],
        "method": "per-tenor z vs trailing history: 0.5(-btc_z)+0.3(pdShare_z)+0.2(-indirect_z); bills x0.35; EWMA(20)",
    }


def auctions_score(result: dict) -> float:
    if not result.get("ok"):
        return 0.0
    idx = result.get("digestion_index") or 0.0
    return float(np.clip(100.0 / (1.0 + np.exp(-(idx - 0.5) * 2.2)), 0.0, 100.0))
