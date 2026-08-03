## The week ahead, continuation

### The call ledger

The ledger opens with this issue. Nothing has resolved yet, so there is no hit rate to quote and the desk does not quote one.

| id | kind | resolves | grading rule |
|---|---|---|---|
| W1-1 | supply | 2026-08-10 | hit if next week's supply table shows that date announced with Treasury's amount and within tolerance, miss if it is announced and outside; open if the row is still projected or its amount is still TBA (a TBA fill is the desk's own estimate and is never graded as announced) |
| W1-2 | srf | 2026-08-10 | hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it |
| W1-3 | reserves | 2026-08-10 | hit if next week's board carries current reserves within tolerance of the target, miss otherwise |
| W1-4 | composite | 2026-08-10 | hit if next week's composite prints inside the band, miss otherwise |
| W1-5 | rde | 2026-08-10 | hit if next week's nowcast still reports the same side of their band, miss if it flips; open if either fit is dark |

### Reserve path assumptions, published beside the path

Start 2,985B on 2026-07-29, trailing drift +5.1B a week over 13 weeks, runoff $0B a month, TGA $970B now against a median of $860B and a p75 of $908B, ON RRP $2.2B, settlements $318B gross counted at 25% passthrough.

- arithmetic on stated assumptions, not a forecast of policy
- the trailing drift already embeds recent QT, settlement and fiscal flows, so the settlement term enters as a deviation from the calendar's own weekly mean (shape, not level) and only the explicit QT pace can still double count against the drift
- settlement drains counted at 25% of gross (rollover assumption) before demeaning

### What the supply table does not know

- rows marked issuance_incomplete sit past the announced horizon on a date whose maturities are known but whose refunding issuance is not projected (quarterly originals have no short cadence to extrapolate); their net is an artifact, not a forecast, and should not be read as a drain
- maturing includes Fed SOMA holdings, which roll over at auction; net new cash to private investors is smaller on SOMA-heavy dates
- Treasury buyback retirements are not netted from the maturing stock
- TBA offering amounts are filled with the tenor's last realized size (amount_estimated=true)
- projected rows carry each tenor's last size forward at its observed cadence; tenors without a regular cadence under 5 weeks (quarterly refunding originals, one-off CMBs) are not projected

The calls above were written before the week ran and are stored in the letter's own state file, so next Monday's issue grades exactly this list and not a convenient subset of it. The board recomputes six times a day; this issue freezes one Monday reading of it. Free public data with native lags. Not investment advice.
