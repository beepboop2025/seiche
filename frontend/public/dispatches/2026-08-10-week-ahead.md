*Issue 2 · the week of 2026-08-10 · the sections run in the same order every week, and section 6 grades what section 5 said last time.*

## 1 · The week in one paragraph

The week opens with the board at **45 out of 100, EROSION**, on 100% coverage. The Tell reads +35, plumbing at the 62nd percentile of its own history against the market's 27th. There are 5 dated items inside the next ten days and 5 calls on the record below.

The single question this week is **whether the +104B of net new cash settling 2026-08-25 shows up in reserves or gets absorbed by the buffers**. Everything under it is either a date, a number the desk expects, or a number the desk got wrong last week.

## 2 · The calendar

| date | event | expected funding impact | what the desk watches |
|---|---|---|---|
| 2026-08-11 (Tue) | Treasury settlement | +39B net new cash, drains reserves | bills 295B and coupons 0B against 256B maturing; announced |
| 2026-08-13 (Thu) | Treasury settlement | +5B net new cash, drains reserves | bills 266B and coupons 0B against 261B maturing; announced |
| 2026-08-17 (Mon) | Treasury settlement | -6B net new cash, returns cash to the market | bills 0B and coupons 125B against 131B maturing; announced |
| 2026-08-18 (Tue) | Treasury settlement | +32B net new cash, drains reserves | bills 297B and coupons 0B against 266B maturing; announced, size taken from the tenor's last print |
| 2026-08-20 (Thu) | Treasury settlement | +17B net new cash, drains reserves | bills 287B and coupons 0B against 270B maturing; announced, size taken from the tenor's last print |

5 dated items inside ten days. The one to diary: Treasury settlement, 2026-08-11 (Tue). Dates are the part of funding stress that is knowable in advance, which is why they lead this letter rather than close it.

## 3 · Supply

| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |
|---|---|---|---|---|---|
| 2026-08-11 (Tue) | 295 | 0 | 256 | **+39** | announced |
| 2026-08-13 (Thu) | 266 | 0 | 261 | **+5** | announced |
| 2026-08-17 (Mon) | 0 | 125 | 131 | **-6** | announced |
| 2026-08-18 (Tue) | 297 | 0 | 266 | **+32** | announced, size estimated |
| 2026-08-20 (Thu) | 287 | 0 | 270 | **+17** | announced, size estimated |
| 2026-08-25 (Tue) | 295 | 77 | 267 | **+104** | projected |
| 2026-08-27 (Thu) | 266 | 0 | 271 | **-5** | projected |
| 2026-08-31 (Mon) | 0 | 164 | 179 | **-15** | projected |
| 2026-09-01 (Tue) | 295 | 0 | 275 | **+20** | projected |
| 2026-09-03 (Thu) | 322 | 42 | 326 | **+38** | projected |

Across the whole 10 row horizon to 2026-09-07, gross issuance runs $2,732B against $2,503B maturing, +229B of net new cash. The heaviest single day is 2026-08-25 (Tue) at +104B. Announcements run through 2026-08-31; 5 rows past that are the desk's own projection, carried at each tenor's last size and its observed cadence, and graded in this letter when Treasury announces.

Net new cash is the number that drains reserves. Maturing includes SOMA rollovers, so the private-side drain runs smaller on SOMA-heavy dates, and Treasury buybacks are not netted out of the maturing stock. Both caveats are in the engine, not in a footnote nobody reads.

## 4 · Reserves

| leg | 13w end level $B | kink crossing |
|---|---|---|
| base | 3,023 | 2026-08-05 |
| fast drain | 2,973 | 2026-08-05 |
| slow | 3,025 | 2026-08-05 |

The legs share one arithmetic and differ on three stated assumptions: a trailing drift of -3.0B a week, a runoff pace of $0B a month, and a TGA now at $929B reverting to its trailing median of $860B on the base leg or its p75 of $910B on the fast one. This is arithmetic on published assumptions, not a forecast of policy, and the trailing drift already embeds recent runoff, so the explicit terms can double count.

The kink itself sits near **$3,703B** of reserves against $2,993B held, $710B below the estimate, on a fit with R² 0.62 and a model versus market consistency of 0.84. Through the kink is where the spread starts answering to reserve changes, so the week's job is watching the slope, not the distance.

External check on the same curve: the NY Fed's latest Reserve Demand Elasticity print (2026-07-06) reads -0.27bp per one percent of reserves with a 68% band of [-0.44, -0.09]; the desk's continuous fit implies -0.30bp, inside that band, and the direction agrees, which the desk publishes 30 days ahead of their release cycle. Across 18 walk forward refits the desk landed inside their 68% band 8 times and agreed on direction 10 times, mean absolute difference 0.26bp.

## 5 · Pre-registered calls

Registered 5 calls for the week of 2026-08-10. Each carries a stable ID, the number the desk expects, the date it resolves and the rule that decides it. Next Monday's issue opens by grading them, misses first.

- **W2-1** · The 2026-08-25 settlement, which the board carries at +104B of net new cash (the board's own projection), lands within $10.4B of that figure once Treasury has announced it. Expected: +104B net new cash, tolerance $10.4B. Resolves 2026-08-17, hit if next week's supply table shows that date announced with Treasury's amount and within tolerance, miss if it is announced and outside; open if the row is still projected or its amount is still TBA (a TBA fill is the desk's own estimate and is never graded as announced).
- **W2-2** · SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. Expected: under $1B; the trailing twenty session maximum today is $0.10B. Resolves 2026-08-17, hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it.
- **W2-3** · Reserves print near $3,044B on next week's H.4.1, the base leg's week one level. Expected: $3,044B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B). Resolves 2026-08-17, hit if next week's board carries current reserves within tolerance of the target, miss otherwise.
- **W2-4** · The composite reads between 42.3 and 46.9 on next week's board. Expected: 42.3 to 46.9, from 45.0 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible. Resolves 2026-08-17, hit if next week's composite prints inside the band, miss otherwise.
- **W2-5** · The desk's continuous reserve demand fit stays inside the NY Fed's published 68% band. Expected: inside [-0.44, -0.09]bp per one percent of reserves; the desk reads -0.30bp today. Resolves 2026-08-17, hit if next week's nowcast still reports the same side of their band, miss if it flips; open if either fit is dark.

## 6 · Last week's calls, graded

| id | verdict | expected | actual | the call |
|---|---|---|---|---|
| W1-3 | **MISS** | $3,038B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B) | $2,993B | Reserves print near $3,038B on next week's H.4.1, the base leg's week one level. |
| W1-2 | **HIT** | under $1B; the trailing twenty session maximum today is $0.10B | $0.10B twenty session maximum | SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. |
| W1-4 | **HIT** | 43.2 to 47.9, from 46.0 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible | 45.0 | The composite reads between 43.2 and 47.9 on next week's board. |
| W1-5 | **HIT** | inside [-0.44, -0.09]bp per one percent of reserves; the desk reads -0.30bp today | inside the band at -0.30bp | The desk's continuous reserve demand fit stays inside the NY Fed's published 68% band. |
| W1-1 | **OPEN** | +107B net new cash, tolerance $10.7B | still projected at +104B, not yet announced | The 2026-08-25 settlement, which the board carries at +107B of net new cash (the board's own projection), lands within $10.7B of that figure once Treasury has announced it. |

Last week: 1 miss, 3 hit, 1 still open. Lifetime the desk has resolved 4 calls and hit 3 of them, 75%. Open calls carry one more week rather than quietly vanish: W1-1.

An open call is one the data could not settle, usually a dark engine or a settlement still carried as projected. It is never scored as a hit.

## 7 · What would change the desk's mind this week

The falsifier ledger travels with the regime and the IDs are stable, so a regular can watch the distance close instead of rereading a static sentence.

- **E1** · The Tell closing back under +15; today it reads +35.
- **E2** · Reserves stabilising for two straight weeks; the current drain runs $-1.8B a business day.
- **E3** · Calendar amplification easing below 1x; today it runs 6.3x.

<!--HAS-DESK-->
