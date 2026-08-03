*Issue 1 · the week of 2026-08-03 · the sections run in the same order every week, and section 6 grades what section 5 said last time.*

## 1 · The week in one paragraph

Monday's reading: **46 out of 100, STRAIN**, on 100% coverage. The Tell reads +32, plumbing at the 68th percentile of its own history against the market's 36th. There are 4 dated items inside the next ten days and 5 calls on the record below.

The single question this week is **whether the +107B of net new cash settling 2026-08-25 shows up in reserves or gets absorbed by the buffers**. Everything under it is either a date, a number the desk expects, or a number the desk got wrong last week.

## 2 · The calendar

| date | event | expected funding impact | what the desk watches |
|---|---|---|---|
| 2026-08-04 (Tue) | Treasury settlement | +61B net new cash, drains reserves | bills 297B and coupons 0B against 237B maturing; announced |
| 2026-08-06 (Thu) | Treasury settlement | +21B net new cash, drains reserves | bills 318B and coupons 0B against 297B maturing; announced |
| 2026-08-11 (Tue) | Treasury settlement | +43B net new cash, drains reserves | bills 299B and coupons 0B against 256B maturing; announced, size taken from the tenor's last print |
| 2026-08-13 (Thu) | Treasury settlement | +23B net new cash, drains reserves | bills 285B and coupons 0B against 261B maturing; announced, size taken from the tenor's last print |

4 dated items inside ten days. The one to diary: Treasury settlement, 2026-08-04 (Tue). Dates are the part of funding stress that is knowable in advance, which is why they lead this letter rather than close it.

## 3 · Supply

| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |
|---|---|---|---|---|---|
| 2026-08-04 (Tue) | 297 | 0 | 237 | **+61** | announced |
| 2026-08-06 (Thu) | 318 | 0 | 297 | **+21** | announced |
| 2026-08-11 (Tue) | 299 | 0 | 256 | **+43** | announced, size estimated |
| 2026-08-13 (Thu) | 285 | 0 | 261 | **+23** | announced, size estimated |
| 2026-08-17 (Mon) | 0 | 112 | 131 | **-19** | announced, size estimated |
| 2026-08-18 (Tue) | 297 | 0 | 266 | **+32** | projected |
| 2026-08-20 (Thu) | 266 | 0 | 270 | **-4** | projected |
| 2026-08-24 (Mon) | 0 | 13 | 0 | **+13** | projected |
| 2026-08-25 (Tue) | 297 | 77 | 267 | **+107** | projected |
| 2026-08-27 (Thu) | 266 | 0 | 271 | **-5** | projected |

Across the whole 11 row horizon to 2026-08-31, gross issuance runs $2,654B against $2,435B maturing, +218B of net new cash. The heaviest single day is 2026-08-25 (Tue) at +107B. Announcements run through 2026-08-17; 6 rows past that are the desk's own projection, carried at each tenor's last size and its observed cadence, and graded in this letter when Treasury announces.

Net new cash is the number that drains reserves. Maturing includes SOMA rollovers, so the private-side drain runs smaller on SOMA-heavy dates, and Treasury buybacks are not netted out of the maturing stock. Both caveats are in the engine, not in a footnote nobody reads.

## 4 · Reserves

| leg | 13w end level $B | kink crossing |
|---|---|---|
| base | 3,161 | 2026-07-29 |
| fast drain | 3,113 | 2026-07-29 |
| slow | 3,163 | 2026-07-29 |

The legs share one arithmetic and differ on three stated assumptions: a trailing drift of +5.1B a week, a runoff pace of $0B a month, and a TGA now at $970B reverting to its trailing median of $860B on the base leg or its p75 of $908B on the fast one. This is arithmetic on published assumptions, not a forecast of policy, and the trailing drift already embeds recent runoff, so the explicit terms can double count.

The kink itself sits near **$3,704B** of reserves against $2,985B held, $719B below the estimate, on a fit with R² 0.62 and a model versus market consistency of 0.76. Through the kink is where the spread starts answering to reserve changes, so the week's job is watching the slope, not the distance.

External check on the same curve: the NY Fed's latest Reserve Demand Elasticity print (2026-07-06) reads -0.27bp per one percent of reserves with a 68% band of [-0.44, -0.09]; the desk's continuous fit implies -0.30bp, inside that band, and the direction agrees, which the desk publishes 23 days ahead of their release cycle. Across 18 walk forward refits the desk landed inside their 68% band 8 times and agreed on direction 10 times, mean absolute difference 0.26bp.

## 5 · Pre-registered calls

Registered 5 calls for the week of 2026-08-03. Each carries a stable ID, the number the desk expects, the date it resolves and the rule that decides it. Next Monday's issue opens by grading them, misses first.

- **W1-1** · The 2026-08-25 settlement, which the board carries at +107B of net new cash (the board's own projection), lands within $10.7B of that figure once Treasury has announced it. Expected: +107B net new cash, tolerance $10.7B. Resolves 2026-08-10, hit if next week's supply table shows that date announced with Treasury's amount and within tolerance, miss if it is announced and outside; open if the row is still projected or its amount is still TBA (a TBA fill is the desk's own estimate and is never graded as announced).
- **W1-2** · SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. Expected: under $1B; the trailing twenty session maximum today is $0.10B. Resolves 2026-08-10, hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it.
- **W1-3** · Reserves print near $3,038B on next week's H.4.1, the base leg's week one level. Expected: $3,038B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B). Resolves 2026-08-10, hit if next week's board carries current reserves within tolerance of the target, miss otherwise.
- **W1-4** · The composite reads between 43.2 and 47.9 on next week's board. Expected: 43.2 to 47.9, from 46.0 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible. Resolves 2026-08-10, hit if next week's composite prints inside the band, miss otherwise.
- **W1-5** · The desk's continuous reserve demand fit stays inside the NY Fed's published 68% band. Expected: inside [-0.44, -0.09]bp per one percent of reserves; the desk reads -0.30bp today. Resolves 2026-08-10, hit if next week's nowcast still reports the same side of their band, miss if it flips; open if either fit is dark.

## 6 · Last week's calls, graded

There is no prior issue to grade. This is the first Week Ahead, so the calls in section 5 are the first entries in the ledger and the next issue opens with them marked hit or miss. Saying that plainly beats printing an empty table.

## 7 · What would change the desk's mind this week

The falsifier ledger travels with the regime and the IDs are stable, so a regular can watch the distance close instead of rereading a static sentence.

- **S1** · SRF or discount window take-up above $1B on a day with no calendar excuse; discount window $6.5B as of 2026-07-29. That is through the $1B line already, so this item reads BREACHED and the standing read is that discount window borrowing is elevated against its own recent history, not that nothing has happened.
- **S2** · A funding-side mover breaching ±3 robust z on a fresh print.
- **S3** · The composite crossing 60; today it reads 46.

<!--HAS-DESK-->
