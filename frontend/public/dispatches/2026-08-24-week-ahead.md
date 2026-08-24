*Issue 4 · the week of 2026-08-24 · the sections run in the same order every week, and section 6 grades what section 5 said last time.*

## 1 · The week in one paragraph

The week opens with the board at **45 out of 100, STRAIN**, on 100% coverage. The Tell reads +23, plumbing at the 58th percentile of its own history against the market's 35th. There are 7 dated items inside the next ten days and 5 calls on the record below.

The single question this week is **whether the +73B of net new cash settling 2026-09-16 shows up in reserves or gets absorbed by the buffers**. Everything under it is either a date, a number the desk expects, or a number the desk got wrong last week.

## 2 · The calendar

| date | event | expected funding impact | what the desk watches |
|---|---|---|---|
| 2026-08-25 (Tue) | Treasury settlement | +33B net new cash, drains reserves | bills 300B and coupons 0B against 267B maturing; announced |
| 2026-08-27 (Thu) | Treasury settlement | -5B net new cash, returns cash to the market | bills 266B and coupons 0B against 271B maturing; announced |
| 2026-08-28 (Fri) | Treasury settlement | +28B net new cash, drains reserves | bills 0B and coupons 28B against 0B maturing; announced |
| 2026-08-31 (Mon) | Treasury settlement | +31B net new cash, drains reserves | bills 0B and coupons 210B against 179B maturing; announced |
| 2026-08-31 (Mon) | month end turn | turn model forecasts +2.2bp of slosh, band [-1.8, +6.9], severity 1 of 5 | SOFR minus IORB into and over the turn; the published number is the naive leg |
| 2026-09-01 (Tue) | Treasury settlement | +28B net new cash, drains reserves | bills 303B and coupons 0B against 275B maturing; announced, size taken from the tenor's last print |
| 2026-09-03 (Thu) | Treasury settlement | +68B net new cash, drains reserves | bills 342B and coupons 53B against 326B maturing; the board's house projection, not yet announced |

7 dated items inside ten days. The one to diary: month end turn, 2026-08-31 (Mon). Dates are the part of funding stress that is knowable in advance, which is why they lead this letter rather than close it.

## 3 · Supply

| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |
|---|---|---|---|---|---|
| 2026-08-25 (Tue) | 300 | 0 | 267 | **+33** | announced |
| 2026-08-27 (Thu) | 266 | 0 | 271 | **-5** | announced |
| 2026-08-28 (Fri) | 0 | 28 | 0 | **+28** | announced |
| 2026-08-31 (Mon) | 0 | 210 | 179 | **+31** | announced |
| 2026-09-01 (Tue) | 303 | 0 | 275 | **+28** | announced, size estimated |
| 2026-09-03 (Thu) | 342 | 53 | 326 | **+68** | projected |
| 2026-09-08 (Tue) | 300 | 0 | 286 | **+14** | projected |
| 2026-09-10 (Thu) | 266 | 0 | 278 | **-12** | projected |
| 2026-09-15 (Tue) | 300 | 0 | 340 | **-40** | projected |
| 2026-09-16 (Wed) | 0 | 73 | 0 | **+73** | projected |

Across the whole 12 row horizon to 2026-09-21, gross issuance runs $2,759B against $2,505B maturing, +254B of net new cash. The heaviest single day is 2026-09-16 (Wed) at +73B. Announcements run through 2026-09-03; 7 rows past that are the desk's own projection, carried at each tenor's last size and its observed cadence, and graded in this letter when Treasury announces.

Net new cash is the number that drains reserves. Maturing includes SOMA rollovers, so the private-side drain runs smaller on SOMA-heavy dates, and Treasury buybacks are not netted out of the maturing stock. Both caveats are in the engine, not in a footnote nobody reads.

## 4 · Reserves

| leg | 13w end level $B | kink crossing |
|---|---|---|
| base | 2,817 | 2026-08-19 |
| fast drain | 2,752 | 2026-08-19 |
| slow | 2,818 | 2026-08-19 |

The legs share one arithmetic and differ on three stated assumptions: a trailing drift of -14.9B a week, a runoff pace of $0B a month, and a TGA now at $936B reverting to its trailing median of $860B on the base leg or its p75 of $926B on the fast one. This is arithmetic on published assumptions, not a forecast of policy, and the trailing drift already embeds recent runoff, so the explicit terms can double count.

The kink itself sits near **$3,703B** of reserves against $2,935B held, $768B below the estimate, on a fit with R² 0.62 and a model versus market consistency of 0.95. Through the kink is where the spread starts answering to reserve changes, so the week's job is watching the slope, not the distance.

External check on the same curve: the NY Fed's latest Reserve Demand Elasticity print (2026-08-13) reads -0.37bp per one percent of reserves with a 68% band of [-0.57, -0.17]; the desk's continuous fit implies -0.29bp, inside that band, and the direction agrees, which the desk publishes 6 days ahead of their release cycle. Across 18 walk forward refits the desk landed inside their 68% band 9 times and agreed on direction 10 times, mean absolute difference 0.25bp.

## 5 · Pre-registered calls

Registered 5 calls for the week of 2026-08-24. Each carries a stable ID, the number the desk expects, the date it resolves and the rule that decides it. Next Monday's issue opens by grading them, misses first.

- **W4-1** · The month end turn on 2026-08-31 prints a slosh inside the model's published band of -1.8 to +6.9bp. Expected: -1.8 to +6.9bp, around a point forecast of +2.2bp (naive leg, severity 1 of 5). Resolves 2026-09-03, hit if the realized slosh for that date on next week's turn record lands inside the band, miss if it lands outside; open if the turn has not yet entered the record.
- **W4-2** · The 2026-09-16 settlement, which the board carries at +73B of net new cash (the board's own projection), lands within $7.3B of that figure once Treasury has announced it. Expected: +73B net new cash, tolerance $7.3B. Resolves 2026-08-31, hit if next week's supply table shows that date announced with Treasury's amount and within tolerance, miss if it is announced and outside; open if the row is still projected or its amount is still TBA (a TBA fill is the desk's own estimate and is never graded as announced).
- **W4-3** · SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. Expected: under $1B; the trailing twenty session maximum today is $0.10B. Resolves 2026-08-31, hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it.
- **W4-4** · Reserves print near $2,986B on next week's H.4.1, the base leg's week one level. Expected: $2,986B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B). Resolves 2026-08-31, hit if next week's board carries current reserves within tolerance of the target, miss otherwise.
- **W4-5** · The composite reads between 42.4 and 47.0 on next week's board. Expected: 42.4 to 47.0, from 45.1 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible. Resolves 2026-08-31, hit if next week's composite prints inside the band, miss otherwise.

## 6 · Last week's calls, graded

| id | verdict | expected | actual | the call |
|---|---|---|---|---|
| W2-1 | **MISS** | +104B net new cash, tolerance $10.4B | +33B announced | The 2026-08-25 settlement, which the board carries at +104B of net new cash (the board's own projection), lands within $10.4B of that figure once Treasury has announced it. |
| W3-2 | **MISS** | $2,964B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B) | $2,935B | Reserves print near $2,964B on next week's H.4.1, the base leg's week one level. |
| W3-1 | **HIT** | under $1B; the trailing twenty session maximum today is $0.10B | $0.10B twenty session maximum | SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. |
| W3-3 | **HIT** | 44.0 to 49.1, from 47.0 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible | 45.1 | The composite reads between 44.0 and 49.1 on next week's board. |
| W3-4 | **HIT** | inside [-0.44, -0.09]bp per one percent of reserves; the desk reads -0.30bp today | inside the band at -0.29bp | The desk's continuous reserve demand fit stays inside the NY Fed's published 68% band. |
| W3-5 | **HIT** | 1% to 11%, from 6% today, taken from the model court's pooled read | 4% | The pooled five day event odds stay between 1% and 11%, that is within five points of today's 6%. |

Last week: 2 miss, 4 hit. Lifetime the desk has resolved 14 calls and hit 9 of them, 64%.

An open call is one the data could not settle, usually a dark engine or a settlement still carried as projected. It is never scored as a hit.

## 7 · What would change the desk's mind this week

The falsifier ledger travels with the regime and the IDs are stable, so a regular can watch the distance close instead of rereading a static sentence.

- **S1** · SRF or discount window take-up above $1B on a day with no calendar excuse; discount window $5.0B as of 2026-08-19. That is through the $1B line already, so this item reads BREACHED and the standing read is that discount window borrowing is elevated against its own recent history, not that nothing has happened.
- **S2** · A funding-side mover breaching ±3 robust z on a fresh print.
- **S3** · The composite crossing 60; today it reads 45.

<!--HAS-DESK-->
