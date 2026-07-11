---
name: seiche-ops-watchdog
description: Health monitoring for a Seiche deployment. Use in scheduled ops checks or when asked "is seiche healthy", "is the data fresh", or when another skill hits a fault, a stale series, or an API error. Reports and escalates; does not repair without an explicit ask.
version: 1.0.0
license: AGPL-3.0
metadata:
  hermes:
    tags: [seiche, ops, monitoring, data-quality]
    related_skills: [seiche-regime-watch]
---

# Seiche ops watchdog

Seiche is fail-loud by design: a stale feed or a gap in the as-published
record is surfaced, never papered over. The watchdog's job is to notice
first and tell the operator plainly.

## The health pass

1. Call `data_health`. For every input series (FRED, NY Fed, OFR, Treasury
   and the rest) it reports freshness, provenance, and fault status.
2. Classify:
   - **Green**: all series fresh, no faults. Say one line and stop.
   - **Amber**: a series stale within its normal publication rhythm (many
     sources publish weekly; a quiet weekend is not an incident). State
     which series, its age, and why it is probably routine.
   - **Red**: a fault flag, a series stale beyond its cadence, an API error,
     or a gap in the as-published (PIT) daily record. The PIT record accrues
     one snapshot per business day; a hole in it is always red, because the
     point-in-time ledger is the product's spine.
3. On red, escalate to the operator immediately with the checklist below.
   Do not wait for the next scheduled pass.

## Escalation message

```
SEICHE OPS — RED: {what, in one line}

Series/component: {name}
Last good: {timestamp from tool output}
Board impact: {which readings are degraded; is the composite still valid}
Likely layer: {upstream source | pull timer | API | unknown}
```

Reference deployment facts that help localize a failure: data pulls run at
12:15 and 20:45 UTC on a systemd timer; the API serves on loopback port 8787
behind a reverse proxy; the store is a single SQLite file. A board that is
uniformly stale right after a pull window points at the puller or its timer,
one stale series points upstream, and an unreachable API points at the
service or proxy.

## Boundaries

- Diagnose from tool output and report. Do not restart services, edit
  timers, or touch the database unless the operator explicitly asks in this
  conversation. When asked, prefer the repo's own ops scripts over improvised
  commands, and say exactly what you ran.
- Quota errors from the MCP endpoint are an account condition, not an
  outage; report them as amber with the reset time (quotas are per UTC day).
- Keep a short memory note of the last pass (status, any open ambers) so
  repeat ambers escalate to red on persistence rather than re-reporting as
  new.
