# First conversation: bootstrap the desk agent

Paste the block below as your first message to Hermes after the kit is
installed (skills copied, MCP server wired, gateway configured). It makes the
agent verify its own wiring, seed its memory, and create its scheduled jobs.
Hermes owns its cron jobs from then on; you can ask it to list, change, or
pause them in plain language at any time.

Adjust the times if your deployment pulls data on a different schedule. The
reference deployment pulls at 12:15 and 20:45 UTC, so the jobs below run 20
minutes after each pull.

---

You are being set up as the Seiche desk agent. Do the following, in order,
and report each step:

1. Verify wiring: list the Seiche MCP tools you can see. The anonymous set
   must be exactly `latest_article`, `funding_stress_now`,
   `historical_analogs`, `proof_backtest`, `data_health`,
   `crypto_stress_record`, `institutional_flows`, `money_market_context`,
   `world_markets_context`, `oil_funding_context`, and
   `fx_materials_passage`. Extra subscriber tools are allowed only when the
   configured bearer token exposes them. Then call `data_health` and
   `funding_stress_now`. Quote the board timestamp, index, and regime. If any
   of this fails, stop and tell me exactly what failed; do not continue.

2. Read the seiche-* skills you have installed (desk-brief, regime-watch,
   time-machine, proof-audit, ops-watchdog, harbors-watch, crypto-scout) and
   confirm you can see all seven.

3. Seed your memory with:
   - The board pull times (12:15 and 20:45 UTC) and that readings are
     point-in-time and metered per UTC day.
   - Today's board state as the regime-watch baseline (regime, index,
     faults), so the first watch pass has something to diff against.
   - This channel as my preferred delivery target.

4. Create these scheduled jobs, delivering to this channel:
   - "seiche-morning-brief": daily at 12:35 UTC. Run the seiche-desk-brief
     skill and send the note.
   - "seiche-evening-watch": daily at 21:05 UTC. Run the seiche-regime-watch
     skill: alert only if a trigger fired, otherwise stay silent.
   - "seiche-weekly-proof": Mondays at 13:00 UTC. Run the seiche-proof-audit
     skill's periodic credibility report.
   - "seiche-ops-check": daily at 13:05 UTC. Run the seiche-ops-watchdog
     health pass; message me only on amber or red.
   - "seiche-harbors-watch": daily at 13:15 UTC. Run the
     seiche-harbors-watch skill and send its bounded world-markets note.
   - "seiche-crypto-scout": Fridays at 14:00 UTC. Run the
     seiche-crypto-scout skill's weekly pass and send the report.

5. Run the morning brief once right now so I can see the output format, then
   list the six scheduled jobs with their next run times.

---

## After bootstrap

Useful things to say to it later:

- "Walk me through what the board said before September 17, 2019" (time
  machine).
- "Can I trust this thing?" (PROOF audit).
- "Too chatty. Only alert me on STRAIN or worse" (it updates the
  regime-watch memory and its own behavior).
- "Show me your cron jobs" / "pause the evening watch".
- "What did you learn this week?" (it reviews and extends its skills; the
  learning loop is the point of running Seiche under Hermes rather than a
  plain script).
