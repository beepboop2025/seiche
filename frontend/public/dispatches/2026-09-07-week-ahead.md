*Issue 6 · the week of 2026-09-07 · the sections run in the same order every week, and section 6 grades what section 5 said last time.*

## 1 · The week in one paragraph

Monday's reading: **45 out of 100, EROSION**, on 100% coverage. The Tell reads +47, plumbing at the 65th percentile of its own history against the market's 18th. There are 7 dated items inside the next ten days and 5 calls on the record below.

The single question this week is **what the FOMC does to the corridor on 2026-09-16, and whether the runoff pace behind the reserve path changes with it**. Everything under it is either a date, a number the desk expects, or a number the desk got wrong last week.

## 2 · The calendar

| date | event | expected funding impact | what the desk watches |
|---|---|---|---|
| 2026-09-08 (Tue) | Treasury settlement | -24B net new cash, returns cash to the market | bills 262B and coupons 0B against 286B maturing; announced |
| 2026-09-10 (Thu) | Treasury settlement | -32B net new cash, returns cash to the market | bills 246B and coupons 0B against 278B maturing; announced |
| 2026-09-15 (Tue) | Treasury settlement | +63B net new cash, drains reserves | bills 284B and coupons 119B against 340B maturing; announced, size taken from the tenor's last print |
| 2026-09-15 (Tue) | Corporate tax date | the TGA builds and reserves fall on a schedule everyone can read | the daily TGA print against the drain the calendar implies |
| 2026-09-16 (Wed) | FOMC decision | the corridor every spread on this board is priced against can move | the IORB and ON RRP settings, the runoff pace, any change to the SRF |
| 2026-09-16 (Wed) | Flagged crunch window | calendar pressure date while reserves sit below the estimated kink | worst case reserves near $2,810B after the drain |
| 2026-09-17 (Thu) | Treasury settlement | -11B net new cash, returns cash to the market | bills 271B and coupons 0B against 282B maturing; announced, size taken from the tenor's last print |

7 dated items inside ten days. The one to diary: FOMC decision, 2026-09-16 (Wed). Dates are the part of funding stress that is knowable in advance, which is why they lead this letter rather than close it.

## 3 · Supply

| settles | bills $B | coupons $B | maturing $B | net new cash $B | status |
|---|---|---|---|---|---|
| 2026-09-08 (Tue) | 262 | 0 | 286 | **-24** | announced |
| 2026-09-10 (Thu) | 246 | 0 | 278 | **-32** | announced |
| 2026-09-15 (Tue) | 284 | 119 | 340 | **+63** | announced, size estimated |
| 2026-09-17 (Thu) | 271 | 0 | 282 | **-11** | announced, size estimated |
| 2026-09-18 (Fri) | 0 | 18 | 0 | **+18** | announced, size estimated |
| 2026-09-22 (Tue) | 262 | 0 | 295 | **-33** | projected |
| 2026-09-24 (Thu) | 246 | 0 | 277 | **-31** | projected |
| 2026-09-25 (Fri) | 0 | 78 | 0 | **+78** | projected |
| 2026-09-29 (Tue) | 262 | 0 | 286 | **-24** | projected |
| 2026-09-30 (Wed) | 0 | 53 | 174 | **-122** | announced, size estimated |

Across the whole 11 row horizon to 2026-10-05, gross issuance runs $2,530B against $2,551B maturing, -20B of net new cash. The heaviest single day is 2026-10-01 (Thu) at +96B. Announcements run through 2026-09-30; 5 rows past that are the desk's own projection, carried at each tenor's last size and its observed cadence, and graded in this letter when Treasury announces.

Net new cash is the number that drains reserves. Maturing includes SOMA rollovers, so the private-side drain runs smaller on SOMA-heavy dates, and Treasury buybacks are not netted out of the maturing stock. Both caveats are in the engine, not in a footnote nobody reads.

## 4 · Reserves

| leg | 13w end level $B | kink crossing |
|---|---|---|
| base | 2,848 | 2026-09-02 |
| fast drain | 2,776 | 2026-09-02 |
| slow | 2,849 | 2026-09-02 |

The legs share one arithmetic and differ on three stated assumptions: a trailing drift of -9.2B a week, a runoff pace of $0B a month, and a TGA now at $944B reverting to its trailing median of $871B on the base leg or its p75 of $944B on the fast one. This is arithmetic on published assumptions, not a forecast of policy, and the trailing drift already embeds recent runoff, so the explicit terms can double count.

The kink itself sits near **$3,703B** of reserves against $2,894B held, $809B below the estimate, on a fit with R² 0.62 and a model versus market consistency of 0.92. Through the kink is where the spread starts answering to reserve changes, so the week's job is watching the slope, not the distance.

External check on the same curve: the NY Fed's latest Reserve Demand Elasticity print (2026-08-13) reads -0.37bp per one percent of reserves with a 68% band of [-0.57, -0.17]; the desk's continuous fit implies -0.29bp, inside that band, and the direction agrees, which the desk publishes 20 days ahead of their release cycle. Across 18 walk forward refits the desk landed inside their 68% band 9 times and agreed on direction 10 times, mean absolute difference 0.25bp.

## 5 · Pre-registered calls

Registered 5 calls for the week of 2026-09-07. Each carries a stable ID, the number the desk expects, the date it resolves and the rule that decides it. Next Monday's issue opens by grading them, misses first.

- **W6-1** · The 2026-09-30 settlement, which the board carries at -122B of net new cash (an announced date sized from the tenor's last print), lands within $12.2B of that figure once Treasury has announced it. Expected: -122B net new cash, tolerance $12.2B. Resolves 2026-09-14, hit if next week's supply table shows that date announced with Treasury's amount and within tolerance, miss if it is announced and outside; open if the row is still projected or its amount is still TBA (a TBA fill is the desk's own estimate and is never graded as announced).
- **W6-2** · SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. Expected: under $1B; the trailing twenty session maximum today is $0.10B. Resolves 2026-09-14, hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it.
- **W6-3** · Reserves print near $2,937B on next week's H.4.1, the base leg's week one level. Expected: $2,937B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B). Resolves 2026-09-14, hit if next week's board carries current reserves within tolerance of the target, miss otherwise.
- **W6-4** · The composite reads between 42.2 and 46.8 on next week's board. Expected: 42.2 to 46.8, from 44.9 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible. Resolves 2026-09-14, hit if next week's composite prints inside the band, miss otherwise.
- **W6-5** · The desk's continuous reserve demand fit stays inside the NY Fed's published 68% band. Expected: inside [-0.57, -0.17]bp per one percent of reserves; the desk reads -0.29bp today. Resolves 2026-09-14, hit if next week's nowcast still reports the same side of their band, miss if it flips; open if either fit is dark.

## 6 · Last week's calls, graded

| id | verdict | expected | actual | the call |
|---|---|---|---|---|
| W5-4 | **MISS** | $2,938B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B) | $2,894B | Reserves print near $2,938B on next week's H.4.1, the base leg's week one level. |
| W4-1 | **HIT** | -1.8 to +6.9bp, around a point forecast of +2.2bp (naive leg, severity 1 of 5) | +4.0bp realized | The month end turn on 2026-08-31 prints a slosh inside the model's published band of -1.8 to +6.9bp. |
| W5-1 | **HIT** | -1.5 to +7.1bp, around a point forecast of +2.5bp (naive leg, severity 1 of 5) | +4.0bp realized | The month end turn on 2026-08-31 prints a slosh inside the model's published band of -1.5 to +7.1bp. |
| W5-3 | **HIT** | under $1B; the trailing twenty session maximum today is $0.10B | $0.10B twenty session maximum | SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. |
| W5-5 | **HIT** | 42.4 to 47.0, from 45.1 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible | 44.9 | The composite reads between 42.4 and 47.0 on next week's board. |
| W4-2 | **OPEN** | +73B net new cash, tolerance $7.3B | that date has left the forward window | The 2026-09-16 settlement, which the board carries at +73B of net new cash (the board's own projection), lands within $7.3B of that figure once Treasury has announced it. |
| W5-2 | **OPEN** | +101B net new cash, tolerance $10.1B | announced, but the amount is still TBA (the board's +63B is the desk's own fill from the tenor's last size, and the desk does not grade itself against its own estimate) | The 2026-09-15 settlement, which the board carries at +101B of net new cash (the board's own projection), lands within $10.1B of that figure once Treasury has announced it. |

Last week: 1 miss, 4 hit, 2 still open. Lifetime the desk has resolved 22 calls and hit 15 of them, 68%. Open calls carry one more week rather than quietly vanish: W5-2. Dropped unresolved after a second week without the data to settle them: W4-2.

An open call is one the data could not settle, usually a dark engine or a settlement still carried as projected. It is never scored as a hit.

## 7 · What would change the desk's mind this week

The falsifier ledger travels with the regime and the IDs are stable, so a regular can watch the distance close instead of rereading a static sentence.

- **E1** · The Tell closing back under +15; today it reads +47.
- **E2** · Reserves stabilising for two straight weeks; the current drain runs $-3.1B a business day.
- **E3** · Calendar amplification easing below 1x; today it runs 6.3x.

<!--HAS-DESK-->
