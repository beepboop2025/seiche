# Seiche editorial proposal controller

This immutable Railway image runs the existing public-data board export and
daily or weekly generators at their reviewed source. The controller pins all
backend files and the original workflow scripts. A source change requires a
reviewed image rebuild. Dependencies use a hash-checked Linux Python 3.12 lock.

The board process receives no credentials. After it exits, its UID-owned orphan
processes are killed and the board is sealed. The article process may receive
only the existing editorial model key and the pinned endpoint/model settings.
No GitHub, Railway, production database, signing or Telegram credential is
passed to either process. Source and previously published editions remain
root-owned. Candidate processes cannot read the controller's environment or
private file descriptors. All writes are checked after those processes stop.

Only today's edition files, the existing indexes, learning feed and desk state
may change. Historical edition/index entries cannot be rewritten. The proposal
uses the original `seiche-desk <desk@seiche.info>` identity, one exact parent,
and `dispatch: ` or `week ahead: ` subject. A private Git bundle, board and proof
are retained under `/evidence`; an intervening main commit fails the proposal.

This prepared controller has no publication authority. `EDITORIAL_APPLY=1`
fails before remote work. It never calls a Telegram API. The current full
publisher's signed active-release gate must pass before a future reviewed
activation can replace the original dispatch-to-publish-to-announce chain.
Do not disable those workflows or schedule this proposal service as a claimed
complete replacement while that gate is unresolved.

Prepare the image with `prepare.py --repository ... --source <full-sha>
--output <new-directory> --signer-public-key <existing-owner-public-key>`.
The selected controller commit must carry the established owner's SSH
signature. Deploy only the generated context to a dedicated Railway service,
with `EDITORIAL_LANE=daily` or `weekly`, one replica, restart policy `NEVER`,
no source autodeployment and no cron during proof. The process deadline is 75
minutes. No `--force`, stale-board or date override is available.
