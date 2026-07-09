# Seiche MCP launch kit

Copy for announcing the MCP endpoint. Pseudonymous (beepboop2025 / the X
account), not the real name. Written in the house voice: plain, human, no dashes,
few hyphens. Edit before posting.

## Positioning (one line)

> Your AI agent can pull macro numbers from anywhere. Seiche tells it the one
> thing the numbers don't: are we in a funding stress regime right now, and what
> happened the last time conditions looked like this.

## Show HN

**Title:** Show HN: Seiche, a funding stress terminal your AI agent can query (MCP)

**Body:**
> AI agents are getting good at investment analysis, but they read raw macro
> data and have to guess at the conclusion. Seiche is the conclusion. It watches
> US dollar funding (Fed balance sheet, repo, bank reserves, Treasury cash) and
> answers four questions any agent doing macro work needs: what is the stress
> regime right now, what are the odds of a funding event in the next few weeks,
> what did the nearest historical days do next, and how often has the model been
> right (with the misses shown, not hidden).
>
> It is all built from free public data, so there is no Bloomberg licence behind
> it and no data cost to pass on. It speaks the Model Context Protocol, so any
> agent (Claude Code, Codex, your own) can call it as a tool. There is a free
> tier you can try with no signup: point your client at
> https://api.seiche.info/mcp and ask for the current read.
>
> Every reading is point in time and every claim is backed by a published
> backtest. Happy to answer questions about the methodology or the honesty layer.

## Product Hunt

**Tagline:** Funding stress readings for AI agents, from free public data.

**Description:**
> Seiche gives your AI agent the judgment layer on top of macro data: the
> current US funding stress regime, forward event odds, the nearest historical
> analogs, and an honest backtest with the misses shown. One MCP endpoint, a free
> tier with no signup, no Bloomberg licence behind it.

## X thread (pseudonymous)

1/
> Most "data for AI agents" launches hand your agent more numbers. The hard part
> was never the numbers. It is the read.
>
> So I built the read. Seiche is a funding stress terminal your agent can query
> as a tool.

2/
> It watches the plumbing of the US dollar: the Fed balance sheet, repo, bank
> reserves, the Treasury cash account. Then it answers the question a macro agent
> actually has: is now a dangerous moment in money markets.

3/
> Four tools, free, no signup:
> current stress regime
> forward odds of a funding event
> the nearest historical analogs and what they did next
> the backtest, with the misses shown not hidden

4/
> All of it from free public data. No Bloomberg licence, so nothing to relicense
> into your agent. Point any MCP client at https://api.seiche.info/mcp and ask.

5/
> The paid tier adds the Time Machine (replay the whole board as of any past
> date, point in time), the positioning read, and a grounded desk assistant.
> Trust in the record stays free forever. The edge is the subscription.

## Reddit (r/mcp, r/LocalLLaMA, r/algotrading)

**Title:** I made a funding stress terminal that AI agents can query over MCP (free tier, no signup)

**Body:** reuse the Show HN body, add "the code and the methodology writeup are
public, links in comments" and drop the link to `docs/MCP.md`.

## Directory blurb (for the aggregator submission forms)

> Seiche is a funding stress early warning terminal for US money markets, exposed
> as MCP tools. It reports the current stress regime, forward event odds, the
> nearest historical analogs, and an honest backtest, all from free public data.
> Free public tier, metered subscriber tiers.

## Sequencing

1. Merge and deploy so the endpoint is live.
2. Publish to the official registry (docs/PUBLISHING.md).
3. Post Show HN on a weekday morning US time. Answer every comment.
4. Same day: X thread, Product Hunt, the subreddits.
5. Submit to the aggregator registries once the official listing is up.
6. A week later: a short writeup of one real call the free tier caught, as the
   follow up. Timely beats promotional.
