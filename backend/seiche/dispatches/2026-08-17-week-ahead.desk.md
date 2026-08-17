## The week ahead, continuation

### The call ledger

The desk has resolved 8 calls across the run of this letter and hit 5 of them, missing 3. The ledger only counts calls the data actually settled; open ones are carried, not quietly counted as wins.

| id | kind | resolves | grading rule |
|---|---|---|---|
| W3-1 | srf | 2026-08-24 | hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it |
| W3-2 | reserves | 2026-08-24 | hit if next week's board carries current reserves within tolerance of the target, miss otherwise |
| W3-3 | composite | 2026-08-24 | hit if next week's composite prints inside the band, miss otherwise |
| W3-4 | rde | 2026-08-24 | hit if next week's nowcast still reports the same side of their band, miss if it flips; open if either fit is dark |
| W3-5 | court | 2026-08-24 | hit if next week's pooled odds sit inside the band, miss otherwise |

### Reserve path assumptions, published beside the path

Start 2,944B on 2026-08-12, trailing drift -12.2B a week over 13 weeks, runoff $0B a month, TGA $959B now against a median of $860B and a p75 of $915B, ON RRP $0.2B, settlements $290B gross counted at 25% passthrough.

- arithmetic on stated assumptions, not a forecast of policy
- the trailing drift already embeds recent QT, settlement and fiscal flows, so the settlement term enters as a deviation from the calendar's own weekly mean (shape, not level) and only the explicit QT pace can still double count against the drift
- settlement drains counted at 25% of gross (rollover assumption) before demeaning

### Still open

- **W1-1** · The 2026-08-25 settlement, which the board carries at +107B of net new cash (the board's own projection), lands within $10.7B of that figure once Treasury has announced it. Status: the supply desk is dark.
- **W2-1** · The 2026-08-25 settlement, which the board carries at +104B of net new cash (the board's own projection), lands within $10.4B of that figure once Treasury has announced it. Status: the supply desk is dark.

The calls above were written before the week ran and are stored in the letter's own state file, so next Monday's issue grades exactly this list and not a convenient subset of it. The board recomputes six times a day; this issue freezes one Monday reading of it. Free public data with native lags. Not investment advice.
