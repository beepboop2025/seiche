Today’s tape did not clear Seiche’s full-story gate. There was no fresh measured change, and the board’s 45.1 STRAIN reading is a Seiche derivation rather than an observed market price. This is therefore a historical replay, not breaking news and not a forecast.

The lesson from the March 2023 regional-bank run is narrower, but important: system dollar-funding capacity and a lender’s ability to meet an immediate cash call are different layers of the same event. Official support can steady aggregate plumbing while a particular balance sheet remains impaired.

## The mechanism or historical setup

In the March 2023 regional-bank run, deposit flight turned unrealised duration losses into an immediate cash problem. The funding system responded through discount-window borrowing and new official facilities, while markets repriced which institutions could survive the same asset-liability mismatch.

The mechanism separates two questions. At the system level, the question is whether dollar funding can be transmitted through the plumbing. At the institution level, the question is whether a lender can meet withdrawals and other obligations with its own deposits, assets, collateral capacity, and funding access. System support can address the first without resolving the second.

That is why the handoff matters. Seiche covers system dollar-funding capacity; [LiquiLens](https://liquilens.in/) covers institution and lender balance-sheet risk; [Undertow](https://liquilens-undertow.com/) covers market liquidity, crowding, and exit capacity.

The selected historical episode is dated 2023-03-13, with construction point-in-time similarity of 0.435 and a lead of 13 days. Those engine fields are comparisons, not publication-vintage backtests or forecasts. They are useful for teaching the mechanism, not for asserting recurrence.

## Quantified evidence with as-of context

The current board supplies the contrast. As of 2026-08-19, reserves were $2,935B, down $8.8B on the week. The ledger attributes $16.0B to the foreign RRP pool, $14.3B to balance-sheet shrinkage, and $10.9B to the residual release. Six legs sum to the reserve change to the dollar, with $8.0B of the balance sheet, or 0.12% of assets, still in an unnamed residual. These are ledger inputs reported in Seiche’s [data overview](https://seiche.info/data/overview.json), not a diagnosis of lender stress.

The reserve-demand estimate placed reserves $768B below a fitted kink as of 2026-08-19. Yet SOFR was 0.9bp below IORB, and the fit R-squared was 0.62. That is a structural estimate set against an observed overnight price: the fitted kink is not an observed threshold at which stress must occur.

The board’s dominant driver is calendar pressure. The dated reserve path contributes 11.0 points, with a saturated score of 100.0. A $266B Treasury auction settlement is scheduled for 2026-08-27 while reserves sit below the estimated kink; a $183B settlement follows on 2026-08-31. The associated worst-case reserve paths are $2,894.8B and $2,879.7B. These are Seiche reserve-path scenarios, not observed reserve prints or predictions.

The official-sector footprint also shifted. Foreign officials cut Fed custody Treasuries by $80.7B over 13 weeks while foreign RRP parking rose $55.6B. The combined official footprint still fell $25.0B to $2,960B. The desk attributes 31% of the custody decline to movement out of the official sector rather than a rotation within it. That may alter the distribution of funding demand; it does not establish a current institutional run.

## The strongest counter-case

The strongest counter-case can defeat the tightening thesis: the current price of overnight cash still says abundance. SOFR was 0.9bp below IORB as of 2026-08-19. The Standing Repo Facility accepted $0.00B as of 2026-08-21, and market stress was at the 35th percentile of its own history as of 2026-08-20.

Those facts matter more than a calendar warning alone. If dollar-funding capacity were broadly tightening now, the cost and use of funding should eventually show it. An overnight rate below IORB, no material use of the backstop, and subdued market-stress positioning argue against declaring a present broad scarcity event.

The board identifies the divergence directly: plumbing is at the 58th percentile and market pricing at the 35th percentile, a gap of 23.3 percentile points. Seiche’s confidence is guarded because modelled or slow-moving structure leads while current market plumbing has not broadly confirmed it.

March 2023 does not erase that rebuttal. It clarifies its boundary. Quiet aggregate plumbing cannot establish that every lender is safe; nor can a historical institutional crisis establish that aggregate dollar funding is currently scarce.

## A falsifiable next test

The observable test arrives with the 2026-08-27 $266B settlement and the 2026-08-31 $183B settlement.

The calendar-tightening thesis weakens if those windows pass while SOFR remains below IORB, Standing Repo Facility use remains not in size, and broader market-stress measures do not confirm pressure. In that outcome, the warning remains a structural and calendar signal rather than an observed funding event.

The thesis gains credibility only if the gap closes in observable behavior: overnight funding changes relative to IORB, the backstop is used in size, or market-stress measures confirm the pressure. Even then, that would describe system conditions. It would not identify the lenders whose balance sheets are vulnerable.

## Follow the pressure chain

Begin with the [Seiche daily dispatch](https://seiche.info/dispatches/2026-08-23-daily) and [Seiche methodology](https://seiche.info/methodology). They distinguish observed inputs, deterministic synthesis, and reserve-path scenarios in the system dollar-funding read.

At the system layer, follow reserves, foreign RRP, Treasury settlement dates, SOFR relative to IORB, and Standing Repo Facility usage through Seiche’s [series index](https://api.seiche.info/api/series/index.json). At the lender layer, the relevant question is whether deposits, asset duration, collateral capacity, and funding access can withstand a cash call. At the market layer, the relevant question is whether crowding and exit capacity change the ability to turn assets into cash.

The March 2023 sequence shows how those layers can diverge: confidence can affect deposits; deposits can expose balance-sheet mismatches; and balance-sheet pressure can drive market repricing while system support is functioning.

## Sources, method, and limits

This replay uses the supplied Seiche dispatch, the [Seiche data overview](https://seiche.info/data/overview.json), and [Seiche methodology](https://seiche.info/methodology). The composite is a Seiche derivation, not an observed market price. Source publication time is not yet collected uniformly, so event, desk-knowledge, and publication clocks remain separate.

**Research boundary:** Seiche covers system dollar-funding capacity. LiquiLens covers institution and lender balance-sheet risk. Undertow covers market liquidity, crowding, and exit capacity. Related Palimpsest readings are context-only and never enter the composite or a model feature.

The historical guide is desk-maintained background. Its engine comparison fields are construction point-in-time comparisons, not publication-vintage backtests or forecasts. Similarity is not recurrence. For continuing system monitoring, consult [Seiche dispatches](https://seiche.info/dispatches/); for the lender-level handoff, consult [LiquiLens](https://liquilens.in/); for the market-liquidity handoff, consult [Undertow](https://liquilens-undertow.com/).

Research and market data, not investment advice.
