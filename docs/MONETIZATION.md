# Monetization Playbook

Extends `STRATEGY.md` (the competitive thesis: own one falsifiable number, publish a
tamper-evident record). This document turns that thesis into revenue lines with
step-by-step launch sequences. Written 2026-07-08.

**The one rule above all lines:** the public scoreboard stays free and the FAILs stay
published, forever. Every revenue line below is a derivative of trust in the record;
the first quietly-edited miss kills all of them at once.

**The master sequence** (lines stack; each gate unlocks the next):

```
Month 0      → daily public record starts (Line 0, free)
Month 1-2    → newsletter paid tier opens (Line 1)
Month 2-4    → education asset built in public (Line 5)
After 60+ days of record AND ≥1 captured stress event:
             → API/institutional outreach (Line 2), consulting funnel (Line 3)
After the Book survives a live stress event with Sharpe intact:
             → signal licensing conversations (Line 4)
Parallel     → India edition (Line 6) rides the LiquiLens track
```

---

## Line 0 — The free daily record (not revenue; the asset everything sells)

**What:** one post per day on X: index level, regime word, one-line attribution
("kink distance 9 days, tails z 2.1"), the P(event, 5bd) number. Every day,
including boring ones — the boring streak is what makes the loud day credible.

**Steps:**
1. Add a "daily card" renderer to the existing publish pipeline (`publish.yml`
   already runs 4-hourly): one PNG — index dial, regime, 3 top engine bars,
   forecast number, tiny 30-day sparkline, the site URL. The pipeline drops it
   into `seiche-site` next to `overview.json`.
2. Post it manually each morning for the first two weeks (learn what people
   engage with), then automate via the X API (or Typefully/Buffer) with a
   hand-written one-liner on top. Manual caption + automated chart is the
   right split: the numbers must be untouched; the voice should be human.
3. Pre-write the calendar threads: Swell/Turn already tell you the
   high-attention windows (quarter-ends, tax dates, auction weeks) weeks in
   advance. Draft those threads early; they are your scheduled credibility
   moments.
4. When a stress event hits with the index elevated beforehand: one thread,
   receipts-first — screenshot of the PIT record from before the event, link
   to the hash chain, no victory lap prose. Let the ledger brag.
5. Bio and pinned tweet: what the number is, where the scoreboard lives, the
   standing disclaimer (see Legal, bottom).

**Cost:** ~30 min/day after setup. **Gate to next line:** none — start now;
every week of delay is a week of provable history that never existed.

---

## Line 1 — Paid newsletter / subscription (first revenue, small but compounding)

**Who pays:** rates nerds, macro-curious professionals, fin-twit plumbing
audience. Expect hundreds of true fans, not tens of thousands — plan for
200–500 subs × $20–35/mo at maturity (= $50–200k/yr), reached over 12–18 months.

**Free vs paid split (the trust-preserving line):**
- FREE forever: headline index, regime, the PROOF scoreboard, the hash chain.
- PAID: full engine board with attribution, the morning brief (already built:
  `brief.py`), real-time alerts relayed to Telegram/Discord (already built:
  `alerts.py` webhook), the forecast layer (Stack number + Venn–Abers band,
  tide-table analogs, swell curve), Time Machine deep-dives, "what changed
  overnight" delta notes.

**Steps:**
1. Platform: Ghost (self-hosted or Ghost Pro) or Substack. Substack = zero
   setup + discovery network; Ghost = your domain, lower fees, API you can
   push briefs into programmatically. Given `brief.py` already emits markdown,
   Ghost's admin API makes the daily paid post a cron job. Recommendation:
   Substack to start (audience discovery), migrate to Ghost past ~300 subs if
   fee math says so.
2. Payments from India: Substack/Stripe handles it if you have Stripe India
   (needs an entity + current account) — OR use a merchant-of-record
   (Lemon Squeezy / Paddle / Gumroad) which handles US/EU sales tax for you.
   Ask a CA about: GST registration, LUT filing for zero-rated export of
   services, and whether sole proprietorship vs LLP fits. One meeting, settled.
3. Wire the paid pipeline: cron → `brief --save` → push markdown to the
   newsletter API → alert webhook → private Telegram channel for subscribers.
4. Launch when: ≥30 days of unbroken daily record AND ≥500 engaged followers.
   Launch price low ($15/mo, founding-member framing), raise for new subs
   later; never raise on existing.
5. Publish a monthly "scoreboard post" — free — grading every call the paid
   tier made. The free grade of the paid product is the marketing.

---

## Line 2 — API / data-feed licensing (the biggest prize per customer)

**Who pays:** systematic funds (index + swell curve as a model input), corporate
treasury desks (alert stream into their Slack/Teams), fintech risk teams
(regime flag in their product). $500–5,000/mo per seat depending on use;
5 customers here ≈ 500 newsletter subs.

**What they get:** authenticated access to what already exists — `/api/overview`
JSON, `/api/alerts`, `/api/series/{mnemonic}`, plus an SLA and a changelog
discipline (breaking-change notice periods). The pitch artifact is the hash
chain: "this feed provably has no survivorship bias."

**Steps:**
1. Productize minimally: API keys (a simple key table + middleware on the
   existing FastAPI app), a `/api/status` uptime endpoint, a one-page data
   dictionary (generate from `config.py` + `sources/base.py` provenance
   fields), and a changelog file. Two weekend days of work.
2. Host properly: the Hetzner deploy (`deploy-hetzner.yml`) already exists;
   add uptime monitoring (UptimeRobot free tier) and a status page.
3. Price three tiers: Research ($500/mo, JSON board, 1 seat), Desk
   ($1,500/mo, board + alerts webhook + series history), Integration
   ($3,000+/mo, redistribution rights inside their org, support SLA).
   Anchor high; institutions distrust cheap data.
4. Outreach only after the gate (60+ days record, one captured event).
   Warm channel first: the people who reply to the daily posts with smart
   questions ARE the buyer persona — DM them, offer a 30-day trial key.
   Cold channel: 20 hand-written emails to rates-desk strategists, treasury
   heads at mid-size corporates, and fintech risk leads, each anchored to a
   specific recent call the record shows ("on <date> the board flagged X
   three days before Y — here's the immutable record").
5. Contract: use a standard data-license template (a lawyer-hour, not a
   lawyer-month): non-redistribution, no-warranty/not-advice, monthly
   auto-renew. Invoice via Stripe/Wise.

---

## Line 3 — "Seiche-for-you" consulting builds (highest near-term $/effort)

**Who pays:** banks, NBFCs, corporates wanting an internal version of exactly
this — their funding dashboard, their early-warning board, on their data,
behind their firewall. India angle: NBFC treasuries entering the term money
market (RBI proposal) need precisely this capability. $15–75k per engagement
depending on scope; 2–3 per year is a business.

**Why you win these:** seiche is a live, public, verifiable demo of the exact
skill being bought — building honest, self-scoring monitoring systems. Nobody
else pitches with a tamper-evident track record of their demo working.

**Steps:**
1. Write the one-page offer doc: "Your funding-stress board, on your data,
   in your perimeter, in 6 weeks" — deliverables (data collectors, 3–5
   engines chosen for their book, composite + regimes, alerting, PIT ledger,
   handover docs), what you need from them (data access, one sponsor), fixed
   price + milestone schedule.
2. Package the codebase for it: the engines are already pure functions of
   Series inputs — that IS the product's portability. Keep a private
   template repo (seiche minus the US-specific sources) ready to fork per
   engagement.
3. Funnel: every institutional conversation from Line 2 that says "can it do
   OUR book?" is a Line 3 lead. Also: one LinkedIn post per month translating
   a seiche finding into treasury-desk language (LinkedIn, not X, is where
   Indian treasury and risk people actually are — your own screenshots
   prove you know this).
4. Scope discipline: fixed deliverable, fixed weeks, their infra, their data
   never touches yours (the LiquiLens code-to-data harness pattern — cite it).
   Change orders for anything beyond.
5. Deliver the first one cheap-ish ($15–20k) for the case study + reference;
   price the second at market.

---

## Line 4 — Signal licensing (large, but strictly gated)

**Who pays:** small/mid systematic funds licensing the Book's daily positioning
signal as one input among many. $2–10k/mo per licensee.

**The gate (do not skip):** the Book's LIVE hash-chained track (not backtest)
must survive at least one real stress event with the walk-forward Sharpe and
drawdown profile intact. Until then this line does not exist. The Book already
prints "does NOT beat the static mix" in bold when it loses — that honesty is
what makes this sellable later.

**Steps (when gated open):**
1. Produce the tearsheet from the ledger: live-since date, daily positions,
   P&L vs every benchmark through the identical pipeline, block-bootstrap
   Sharpe CI, per-episode attribution. All of this already exists in
   `book.py` output — format it.
2. License the SIGNAL, never manage money: impersonal, delivered
   machine-readably, same time daily, to institutions only. Managing money
   or personalized advice triggers registration regimes (SEBI PMS/IA in
   India, SEC/CFTC in the US) — licensing an impersonal feed to institutions
   is the clean lane. Confirm the lane with a securities lawyer once, before
   the first contract.
3. Distribution: direct (the Line 2 relationships), or signal marketplaces
   as a discovery channel (with care — exclusivity clauses).
4. Cap licensees deliberately (scarcity is part of the price) and disclose
   the cap.

---

## Line 5 — Education (side revenue, best top-of-funnel)

**Who pays:** the same audience as Line 1, plus corporate teams. Market
plumbing is hot and badly taught; almost nobody teaches it with a live
instrument attached.

**Products:**
- Cohort or self-paced course: "How the dollar funding system actually
  works" — 6 modules that walk the seiche engines (reserve demand → the kink;
  calendar physics → swell; dealer balance sheets → warehouse; the rescuer →
  breakwater). $200–500 self-paced; $1–2k cohort.
- Corporate workshop: one-day version for treasury/risk teams, $5–15k.

**Steps:**
1. Build it in public: each course module starts as a free X thread/long-post
   (which is ALSO Line 0 content). The threads that perform become the
   curriculum — audience-validated before you record anything.
2. Record against the live tool, not slides: every concept demonstrated on
   the actual board and the Time Machine. That's the moat vs generic courses.
3. Platform: Teachable/Podia, or Ghost's paid-post course pattern to keep one
   stack. Merchant-of-record handles tax as in Line 1.
4. The corporate workshop is the Line 3 wedge: half the rooms that book a
   workshop will ask "can you build this for us?"

---

## Line 6 — The India edition (the strategic one)

**What:** an India funding-stress board — corridor-relative spreads, LAF/MSF/
SDF liquidity, GST/advance-tax calendar physics, term-money tenors as NBFCs
enter that market. Nobody publishes this. The LiquiLens work already banked
most of the parts: the M5 concept map, the decade of policy corridor, the
verified daily liquidity backfill + forward accumulator, the +5bp/₹1-lakh-crore
sensitivity, the RBI MMO scraper.

**Why it's the strategic line:** it monetizes at Indian B2B prices (Lines 2+3
against NBFC treasuries — a customer base LiquiLens already targets), while
global seiche provides the public credibility. The two products share one
proof style: hash-chained, self-scoring, failure-publishing.

**Steps:**
1. Build M5 shadow-mode-first (per the LiquiLens plan): engines + composite
   publishing a daily number into the PIT ledger from day one; historical
   replay validation added when the 2017→ data lands.
2. Run the same Line 0 playbook on the India number — on LinkedIn as well as
   X, because that's where Indian treasury people live.
3. Sell it as the Line 2/3 offering to NBFC treasuries; the RBI term-money
   change is the door-opener conversation.

---

## Cross-cutting setup (do once, first week)

1. **Entity + money rails:** CA meeting — entity choice, GST registration,
   LUT for export-of-services, Stripe India vs merchant-of-record. One
   meeting, one checklist, done.
2. **Standing disclaimer** (site footer, newsletter footer, API terms, course
   T&Cs): educational/informational market commentary; impersonal; not
   investment/financial advice; no offer or solicitation; past performance ≠
   future results. Have the lawyer-hour bless the exact wording, plus the
   SEBI research-analyst perimeter question (India-based publisher, US-market
   impersonal commentary) and the signal-licensing lane (Line 4) when relevant.
3. **The streak insurance:** the daily card must not depend on you being
   awake — it's already a cron; make the X post survivable too (queue 2–3
   fallback cards). A broken streak is a broken story.
4. **One metrics sheet:** subs, MRR, API seats, record length (days), events
   captured/missed. Review monthly; kill lines that don't move after two
   quarters of honest effort.

## What NOT to do

- Don't paywall the scoreboard or the hash chain. Ever.
- Don't manage anyone's money, take discretionary mandates, or give
  personalized advice — every line here is impersonal publishing/licensing.
- Don't launch paid before 30 days of unbroken public record.
- Don't quote the Book's backtest to sell Line 4 — live record only.
- Don't build new engines to sell subscriptions; sell what already runs.
  Engineering time goes to reliability (the streak) before features.
