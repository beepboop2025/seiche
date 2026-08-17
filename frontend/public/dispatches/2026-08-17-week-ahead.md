*Issue 3 · the week of 2026-08-17 · the sections run in the same order every week, and section 6 grades what section 5 said last time.*

## 1 · The week in one paragraph

Monday's reading: **47 out of 100, STRAIN**, on 94% coverage. The Tell reads +37, plumbing at the 64th percentile of its own history against the market's 27th. There are 1 dated items inside the next ten days and 5 calls on the record below.

The single question this week is **whether the spread starts answering to reserve changes now that the system is $759B through the fitted kink**. Everything under it is either a date, a number the desk expects, or a number the desk got wrong last week.

## 2 · The calendar

| date | event | expected funding impact | what the desk watches |
|---|---|---|---|
| 2026-08-20 (Thu) | Auction settlement | $266B settles, reserves fall as the TGA builds | the supply desk is dark, so this is gross settlement and not net new cash |

1 dated items inside ten days. The one to diary: Auction settlement, 2026-08-20 (Thu). Dates are the part of funding stress that is knowable in advance, which is why they lead this letter rather than close it.

## 3 · Supply

The supply desk is dark this week (no auction history). When it is live this section carries the forward cash table: every settlement date with bills, coupons and maturing paper, and the net new cash that is the reserve drain the calendar forces, with announced rows kept visibly apart from the house projection.

## 4 · Reserves

| leg | 13w end level $B | kink crossing |
|---|---|---|
| base | 2,885 | 2026-08-12 |
| fast drain | 2,830 | 2026-08-12 |
| slow | 2,885 | 2026-08-12 |

The legs share one arithmetic and differ on three stated assumptions: a trailing drift of -12.2B a week, a runoff pace of $0B a month, and a TGA now at $959B reverting to its trailing median of $860B on the base leg or its p75 of $915B on the fast one. This is arithmetic on published assumptions, not a forecast of policy, and the trailing drift already embeds recent runoff, so the explicit terms can double count.

The kink itself sits near **$3,703B** of reserves against $2,944B held, $759B below the estimate, on a fit with R² 0.62 and a model versus market consistency of 0.89. Through the kink is where the spread starts answering to reserve changes, so the week's job is watching the slope, not the distance.

External check on the same curve: the NY Fed's latest Reserve Demand Elasticity print (2026-07-06) reads -0.27bp per one percent of reserves with a 68% band of [-0.44, -0.09]; the desk's continuous fit implies -0.30bp, inside that band, and the direction agrees, which the desk publishes 37 days ahead of their release cycle. Across 18 walk forward refits the desk landed inside their 68% band 8 times and agreed on direction 10 times, mean absolute difference 0.26bp.

## 5 · Pre-registered calls

Registered 5 calls for the week of 2026-08-17. Each carries a stable ID, the number the desk expects, the date it resolves and the rule that decides it. Next Monday's issue opens by grading them, misses first.

- **W3-1** · SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. Expected: under $1B; the trailing twenty session maximum today is $0.10B. Resolves 2026-08-24, hit if next week's board shows a twenty session maximum take-up under the threshold, miss if any session prints at or above it.
- **W3-2** · Reserves print near $2,964B on next week's H.4.1, the base leg's week one level. Expected: $2,964B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B). Resolves 2026-08-24, hit if next week's board carries current reserves within tolerance of the target, miss otherwise.
- **W3-3** · The composite reads between 44.0 and 49.1 on next week's board. Expected: 44.0 to 49.1, from 47.0 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible. Resolves 2026-08-24, hit if next week's composite prints inside the band, miss otherwise.
- **W3-4** · The desk's continuous reserve demand fit stays inside the NY Fed's published 68% band. Expected: inside [-0.44, -0.09]bp per one percent of reserves; the desk reads -0.30bp today. Resolves 2026-08-24, hit if next week's nowcast still reports the same side of their band, miss if it flips; open if either fit is dark.
- **W3-5** · The pooled five day event odds stay between 1% and 11%, that is within five points of today's 6%. Expected: 1% to 11%, from 6% today, taken from the model court's pooled read. Resolves 2026-08-24, hit if next week's pooled odds sit inside the band, miss otherwise.

## 6 · Last week's calls, graded

| id | verdict | expected | actual | the call |
|---|---|---|---|---|
| W2-3 | **MISS** | $3,044B, tolerance $25B (the width of the desk's own fast to slow bracket at week one, floored at $25B) | $2,944B | Reserves print near $3,044B on next week's H.4.1, the base leg's week one level. |
| W2-4 | **MISS** | 42.3 to 46.9, from 45.0 today; the band is the board's own Monte Carlo p10 to p90 at 5 sessions, seeded fixed so the band is reproducible | 47.0 | The composite reads between 42.3 and 46.9 on next week's board. |
| W2-2 | **HIT** | under $1B; the trailing twenty session maximum today is $0.10B | $0.10B twenty session maximum | SRF take-up stays under $1B on every session of the week, which is to say it stays below the de minimis line. |
| W2-5 | **HIT** | inside [-0.44, -0.09]bp per one percent of reserves; the desk reads -0.30bp today | inside the band at -0.30bp | The desk's continuous reserve demand fit stays inside the NY Fed's published 68% band. |
| W1-1 | **OPEN** | +107B net new cash, tolerance $10.7B | the supply desk is dark | The 2026-08-25 settlement, which the board carries at +107B of net new cash (the board's own projection), lands within $10.7B of that figure once Treasury has announced it. |
| W2-1 | **OPEN** | +104B net new cash, tolerance $10.4B | the supply desk is dark | The 2026-08-25 settlement, which the board carries at +104B of net new cash (the board's own projection), lands within $10.4B of that figure once Treasury has announced it. |

Last week: 2 miss, 2 hit, 2 still open. Lifetime the desk has resolved 8 calls and hit 5 of them, 62%. Open calls carry one more week rather than quietly vanish: W2-1. Dropped unresolved after a second week without the data to settle them: W1-1.

An open call is one the data could not settle, usually a dark engine or a settlement still carried as projected. It is never scored as a hit.

## 7 · What would change the desk's mind this week

The falsifier ledger travels with the regime and the IDs are stable, so a regular can watch the distance close instead of rereading a static sentence.

- **S1** · SRF or discount window take-up above $1B on a day with no calendar excuse; discount window $5.6B as of 2026-08-12. That is through the $1B line already, so this item reads BREACHED and the standing read is that discount window borrowing is elevated against its own recent history, not that nothing has happened.
- **S2** · A funding-side mover breaching ±3 robust z on a fresh print.
- **S3** · The composite crossing 60; today it reads 47.

<!--HAS-DESK-->
