## The week ahead, continuation

### The call ledger

The desk has resolved 17 calls across the run of this letter and hit 11 of them, missing 6. The ledger only counts calls the data actually settled; open ones are carried, not quietly counted as wins.

| id | kind | resolves | grading rule |
|---|---|---|---|
| W5-1 | turn | 2026-09-03 | hit if the realized slosh for that date on next week's turn record lands inside the band, miss if it lands outside; open if the turn has not yet entered the record |
| W5-2 | supply | 2026-09-07 | hit if next week's supply table shows that date announced with Treasury's amount and within tolerance, miss if it is announced and outside; open if the row is still projected or its amount is still TBA (a TBA fill is the desk's own estimate and is never graded as announced) |
| W5-3 | srf | 2026-09-07 | hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it |
| W5-4 | reserves | 2026-09-07 | hit if next week's board carries current reserves within tolerance of the target, miss otherwise |
| W5-5 | composite | 2026-09-07 | hit if next week's composite prints inside the band, miss otherwise |

### Reserve path assumptions, published beside the path

Start 2,925B on 2026-08-26, trailing drift -10.9B a week over 13 weeks, runoff $0B a month, TGA $959B now against a median of $864B and a p75 of $935B, ON RRP $0.2B, settlements $137B gross counted at 25% passthrough.

- arithmetic on stated assumptions, not a forecast of policy
- the trailing drift already embeds recent QT, settlement and fiscal flows, so the settlement term enters as a deviation from the calendar's own weekly mean (shape, not level) and only the explicit QT pace can still double count against the drift
- settlement drains counted at 25% of gross (rollover assumption) before demeaning

### What the supply table does not know

- rows marked issuance_incomplete sit past the announced horizon on a date whose maturities are known but whose refunding issuance is not projected (quarterly originals have no short cadence to extrapolate); their net is an artifact, not a forecast, and should not be read as a drain
- maturing includes Fed SOMA holdings, which roll over at auction; net new cash to private investors is smaller on SOMA-heavy dates
- Treasury buyback retirements are not netted from the maturing stock
- TBA offering amounts are filled with the tenor's last realized size (amount_estimated=true)
- projected rows carry each tenor's last size forward at its observed cadence; tenors without a regular cadence under 5 weeks (quarterly refunding originals, one-off CMBs) are not projected

### Still open

- **W4-1** · The month end turn on 2026-08-31 prints a slosh inside the model's published band of -1.8 to +6.9bp. Status: the turn is not in the board's record yet.
- **W4-2** · The 2026-09-16 settlement, which the board carries at +73B of net new cash (the board's own projection), lands within $7.3B of that figure once Treasury has announced it. Status: that date has left the forward window.

The calls above were written before the week ran and are stored in the letter's own state file, so next Monday's issue grades exactly this list and not a convenient subset of it. The board recomputes six times a day; this issue freezes one Monday reading of it. Free public data with native lags. Not investment advice.
