---
name: rissaga-desk-reads
description: Publish the selected Rissaga v2 desk reads to the free Liquidity Lab channel. Use each route's own voice, keep numbers verbatim, and acknowledge each delivery revision.
version: 2.0.1
---

# rissaga-desk-reads

Rissaga writes `/var/lib/rissaga/latest.json`. The v2 handoff keeps the old
`items` and `channel_candidates` fields, and adds `story_id`, `dispatch_id`
plus `routes`.
Each route names one desk, its best matching beat, its live `desk_line`, an
editorial `angle`, deterministic `fallback_commentary`, and a
`channel_candidate` flag. The shared channel gets at most two selected routes
per sweep. All other routes are for the desk bots and must not be copied into
the shared channel by this skill.

## Steps

1. Read `/var/lib/rissaga/latest.json`. If it is missing, unreadable, or its
   `generated` timestamp is older than 8 hours, reply exactly
   `handoff stale or missing, nothing posted` and stop.
2. Build the work list per item:

   * For a v2 handoff, select only routes with `channel_candidate: true`.
   * Refuse the run if more than two routes are selected globally or more than
     one route is selected on one item. Reply
     `handoff route cap invalid, nothing posted` and stop.
   * For a legacy handoff without routes, use each index in
     `channel_candidates` and the item's original primary desk fields.
   * If the work list is empty, reply exactly
     `no channel candidates this run` and stop.

3. Read `/home/hermes/.hermes/state/rissaga_posted.json`; it may not exist.
   Delivery state is per item revision, never per run. The v2 key is
   `DISPATCH_ID:DESK`. If an older v2 item has no `dispatch_id`, fall back to
   `STORY_ID:DESK`. For a legacy item without either identifier, use
   `GENERATED:ITEM_INDEX:DESK`.
   Skip only keys already present in the marker's `posted` object. Also honor
   the old single `generated` marker when it exactly matches this handoff, so
   an upgrade cannot replay an already completed v1 run.
4. Handle every unposted work item independently. Compose one Telegram HTML
   message of exactly four lines:

   line 1: `\U0001f30a <b>Rissaga</b> [DESK · LABEL]`

   line 2: `<a href="LINK">TITLE</a>`, with title and link HTML escaped

   line 3: `SOURCE plus N more outlets, AGE` when `n_sources` is above 1,
   otherwise `SOURCE, AGE`

   line 4: `DESK_NICE desk read: DESK_LINE. COMMENTARY`

   `DESK`, `LABEL`, `DESK_NICE`, `DESK_LINE`, and the route selection come
   from that same route. Never combine two routes or two stories in one post.

5. Write `COMMENTARY` in the selected desk's register:

   * SEICHE: funding mechanics, facilities, reserves and plumbing.
   * LIQUILENS: institution balance sheets, thresholds and failure paths.
   * UNDERTOW: quoted depth, exit cost and market carrying capacity.
   * CORPORATE: transmission from funding access into company cash flows.
   * REALECON: households, employment, prices and downstream demand.
   * PALIMPSEST: distinguish network blocking, content deletion and model
     refusal. State measurement health or limitations before inference. Never
     turn a censorship observation into a market prediction.
   * RIPTIDE: distinguish a one-session shock from persistence across
     volatility, spreads and trend. News is advisory only, and paper sizing
     changes only from permitted cues.

   Use only facts and numbers already present in this item's `desk_line`,
   title, source line and angle. Never invent, recompute or round a number.
   No prediction, advice, first person, exclamation, or emoji beyond line 1.
   Use commas, colons or parentheses, never an em dash or en dash. Keep the
   added sentence to at most 28 words. If a safe original sentence is not
   possible, use this route's `fallback_commentary` verbatim.
6. For each item, write only its four lines to a temporary file and publish:

   ```sh
   post_file=$(mktemp /tmp/rissaga-post.XXXXXX)
   # write the four-line message to "$post_file"
   lab-channel-post --text-file "$post_file"
   ```

   The helper reads the bot token. Never echo, read, log, or place the token
   in a URL or command line.
7. Immediately after each successful helper call, update
   `/home/hermes/.hermes/state/rissaga_posted.json` atomically. Preserve its
   other keys and set `posted[DELIVERY_KEY]` to the handoff's `generated`
   value. If a later item fails, stop and report the failure; already
   successful items stay marked, and a retry handles only the remainder.
8. Reply with newly posted titles only, one per line. If every selected item
   was already marked, reply exactly `already posted for these items`.

## Refusals

Never improvise a story, route, desk line, number, or channel candidate. An
absent radar run, malformed route set, stale handoff, or failed helper remains
visible as an absence or failure.
