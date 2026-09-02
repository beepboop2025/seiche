*Issue 5 · the week of 2026-08-31 · the sections run in the same order every week, and section 6 grades what section 5 said last time.*

## 1 · The week in one paragraph

The week opens with the board at **45 out of 100, STRAIN**, on 100% coverage. The Tell reads +44, plumbing at the 58th percentile of its own history against the market's 14th. There are 6 dated items inside the next ten days and 5 calls on the record below.

The single question this week is **whether the +101B of net new cash settling 2026-09-15 shows up in reserves or gets absorbed by the buffers**. Everything under it is either a date, a number the desk expects, or a number the desk got wrong last week.

## 2 · The calendar

| date | event | expected funding impact | what the desk watches |
|---|---|---|---|
| 2026-08-31 (Mon) | Treasury settlement | +54B net new cash, drains reserves | bills 0B and coupons 234B against 179B maturing; announced |
| 2026-08-31 (Mon) | month end turn | turn model forecasts +2.5bp of slosh, band [-1.5, +7.1], severity 1 of 5 | SOFR minus IORB into and over the turn; the published number is the naive leg |
| 2026-09-01 (Tue) | Treasury settlement | +9B net new cash, drains reserves | bills 284B and coupons 0B against 275B maturing; announced |
| 2026-09-03 (Thu) | Treasury settlement | -8B net new cash, returns cash to the market | bills 318B and coupons 0B against 326B maturing; announced |
| 2026-09-08 (Tue) | Treasury settlement | +14B net new cash, drains reserves | bills 300B and coupons 0B against 286B maturing; announced, size taken from the tenor's last print |
| 2026-09-10 (Thu) | Treasury settlement | +0B net new cash, drains reserves | bills 278B and coupons 0B against 278B maturing; announced, size taken from the tenor's last print |

6 dated items inside ten days. The one to diary: month end turn, 2026-08-31 (Mon). Dates are the part of funding stress that is knowable in advance, which is why they lead this letter rather than close it.

## 3 · Supply

| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |
|---|---|---|---|---|---|
| 2026-08-31 (Mon) | 0 | 234 | 179 | **+54** | announced |
| 2026-09-01 (Tue) | 284 | 0 | 275 | **+9** | announced |
| 2026-09-03 (Thu) | 318 | 0 | 326 | **-8** | announced |
| 2026-09-08 (Tue) | 300 | 0 | 286 | **+14** | announced, size estimated |
| 2026-09-10 (Thu) | 278 | 0 | 278 | **+0** | announced, size estimated |
| 2026-09-15 (Tue) | 284 | 157 | 340 | **+101** | projected |
| 2026-09-17 (Thu) | 266 | 0 | 282 | **-16** | projected |
| 2026-09-22 (Tue) | 284 | 0 | 295 | **-10** | projected |
| 2026-09-24 (Thu) | 266 | 0 | 277 | **-10** | projected |
| 2026-09-25 (Fri) | 0 | 78 | 0 | **+78** | projected |

Across the whole 10 row horizon to 2026-09-28, gross issuance runs $2,750B against $2,537B maturing, +212B of net new cash. The heaviest single day is 2026-09-15 (Tue) at +101B. Announcements run through 2026-09-15; 5 rows past that are the desk's own projection, carried at each tenor's last size and its observed cadence, and graded in this letter when Treasury announces.

Net new cash is the number that drains reserves. Maturing includes SOMA rollovers, so the private-side drain runs smaller on SOMA-heavy dates, and Treasury buybacks are not netted out of the maturing stock. Both caveats are in the engine, not in a footnote nobody reads.

## 4 · Reserves

| leg | 13w end level $B | kink crossing |
|---|---|---|
| base | 2,878 | 2026-08-26 |
| fast drain | 2,808 | 2026-08-26 |
| slow | 2,878 | 2026-08-26 |

The legs share one arithmetic and differ on three stated assumptions: a trailing drift of -10.9B a week, a runoff pace of $0B a month, and a TGA now at $959B reverting to its trailing median of $864B on the base leg or its p75 of $935B on the fast one. This is arithmetic on published assumptions, not a forecast of policy, and the trailing drift already embeds recent runoff, so the explicit terms can double count.

The kink itself sits near **$3,704B** of reserves against $2,925B held, $779B below the estimate, on a fit with R² 0.62 and a model versus market consistency of 0.94. Through the kink is where the spread starts answering to reserve changes, so the week's job is watching the slope, not the distance.

External check on the same curve: the NY Fed's latest Reserve Demand Elasticity print (2026-08-13) reads -0.37bp per one percent of reserves with a 68% band of [-0.57, -0.17]; the desk's continuous fit implies -0.29bp, inside that band, and the direction agrees, which the desk publishes 13 days ahead of their release cycle. Across 18 walk forward refits the desk landed inside their 68% band 9 times and agreed on direction 10 times, mean absolute difference 0.25bp.

## 5 · Pre-registered calls

Registered 5 calls for the week of 2026-08-31. Each carries a stable ID, the number the desk expects, the date it resolves and the rule that decides it. Next Monday's issue opens by grading them, misses first.

- **W5-1** · The month end turn on 2026-08-31 prints a slosh inside the model's published band of -1.5 to +7.1bp. Expected: -1.5 to +7.1bp, around a point forecast of +2.5bp (naive leg, severity 1 of 5). Resolves 2026-09-03, hit if the realized slosh for that date on next week's turn record lands inside the band, miss if it lands outside; open if the turn has not yet entered the record.
- **W5-2** · The 2026-09-15 settlement, which the board carries at +101B of net new cash (the board's own projection), lands within $10.1B of that figure once Treasury has announced it. Expected: +101B net new cash, tolerance $10.1B. Resolves 2026-09-07, hit if next week's supply table shows that date announced with Treasury's amount and within tolerance, miss if it is announced and outside; open if the row is still projected or its amount is still TBA (a TBA fill is the desk's own estimate and is never graded as announced).
- **W5-3** · SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. Expected: under $1B; the trailing twenty session maximum today is $0.10B. Resolves 2026-09-07, hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it.
- **W5-4** · Reserves print near $2,938B on next week's H.4.1, the base leg's week one level. Expected: $2,938B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B). Resolves 2026-09-07, hit if next week's board carries current reserves within tolerance of the target, miss otherwise.
- **W5-5** · The composite reads between 42.4 and 47.0 on next week's board. Expected: 42.4 to 47.0, from 45.1 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible. Resolves 2026-09-07, hit if next week's composite prints inside the band, miss otherwise.

## 6 · Last week's calls, graded

| id | verdict | expected | actual | the call |
|---|---|---|---|---|
| W4-4 | **MISS** | $2,986B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B) | $2,925B | Reserves print near $2,986B on next week's H.4.1, the base leg's week one level. |
| W4-3 | **HIT** | under $1B; the trailing twenty session maximum today is $0.10B | $0.10B twenty session maximum | SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. |
| W4-5 | **HIT** | 42.4 to 47.0, from 45.1 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible | 45.1 | The composite reads between 42.4 and 47.0 on next week's board. |
| W4-1 | **OPEN** | -1.8 to +6.9bp, around a point forecast of +2.2bp (naive leg, severity 1 of 5) | the turn is not in the board's record yet | The month end turn on 2026-08-31 prints a slosh inside the model's published band of -1.8 to +6.9bp. |
| W4-2 | **OPEN** | +73B net new cash, tolerance $7.3B | that date has left the forward window | The 2026-09-16 settlement, which the board carries at +73B of net new cash (the board's own projection), lands within $7.3B of that figure once Treasury has announced it. |

Last week: 1 miss, 2 hit, 2 still open. Lifetime the desk has resolved 17 calls and hit 11 of them, 65%. Open calls carry one more week rather than quietly vanish: W4-1, W4-2.

An open call is one the data could not settle, usually a dark engine or a settlement still carried as projected. It is never scored as a hit.

## 7 · What would change the desk's mind this week

The falsifier ledger travels with the regime and the IDs are stable, so a regular can watch the distance close instead of rereading a static sentence.

- **S1** · SRF or discount window take-up above $1B on a day with no calendar excuse; discount window $4.9B as of 2026-08-26. That is through the $1B line already, so this item reads BREACHED and the standing read is that discount window borrowing is elevated against its own recent history, not that nothing has happened.
- **S2** · A funding-side mover breaching ±3 robust z on a fresh print.
- **S3** · The composite crossing 60; today it reads 45.

<!--HAS-DESK-->
