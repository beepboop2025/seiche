---
name: rissaga-desk-reads
description: Publish the Rissaga desk reads to the free Liquidity Lab channel from the radar's latest.json handoff. Grounded interpretation only, numbers verbatim, never invent a figure. Runs on the rissaga-desk-reads cron 15 minutes after each radar sweep.
version: 1.0.0
---

# rissaga-desk-reads

Rissaga is the lab's deterministic news radar. Every 6 hours it marks the
few news items that matter and writes `/var/lib/rissaga/latest.json` with,
per item: title, link, source, age, the owning desk (SEICHE, LIQUILENS,
UNDERTOW, CORPORATE, REALECON), and `desk_line`, the desk's live board
numbers already composed as ground truth. This skill turns the channel
worthy items into channel posts, each with a one sentence desk read.

## Steps

1. `cat /var/lib/rissaga/latest.json`
2. If `channel_candidates` is an empty list, reply exactly
   "no channel candidates this run" and stop. Never post filler.
3. `cat /home/hermes/.hermes/state/rissaga_posted.json` (it may not exist).
   If its `generated` value equals the handoff's `generated`, reply
   "already posted for this run" and stop. A run is posted at most once.
4. For each index in `channel_candidates` (2 at most), take that item from
   `items` and compose ONE Telegram HTML message, exactly four lines:

   line 1: `\U0001f30a <b>Rissaga</b> [DESK · LABEL]` using the item's
   `desk` and `label` verbatim.
   line 2: `<a href="LINK">TITLE</a>` with the title HTML escaped.
   line 3: `SOURCE plus N more outlets, AGE` when `n_sources` is above 1,
   else `SOURCE, AGE`.
   line 4: `DESK_NICE desk read: DESK_LINE. INTERPRETATION`

   The INTERPRETATION is one added sentence for that desk and it must obey
   every rule here: use only numbers that already appear in `desk_line`,
   never invent, recompute or round a number, no prediction, no advice, no
   first person, no exclamation, no emoji beyond line 1, commas colons or
   parentheses only, NEVER an em dash or en dash, at most 28 words, plain
   desk register (the reader is a market professional). Say what the board
   context means for how that desk watches this story, nothing more.

5. Write each message to a file and publish it with the helper, which
   appends the footer and buttons itself:

   ```
   cat > /tmp/rissaga_post.txt << 'MSG'
   ...the four lines...
   MSG
   lab-channel-post --text-file /tmp/rissaga_post.txt
   ```

   The helper reads the bot token itself. NEVER echo, cat, or log the
   token, never place it in a URL or command line.

6. On success write the marker:
   `printf '{"generated": "GENERATED_VALUE"}' > /home/hermes/.hermes/state/rissaga_posted.json`
7. Reply with the posted titles only, one per line, for the delivery log.

## Refusals

If latest.json is missing, unreadable, or its `generated` timestamp is
older than 8 hours, reply "handoff stale or missing, nothing posted" and
stop. An absent radar run must never be papered over with an improvised
post.
