# Seiche static publication controller

Build this isolated controller from reviewed signed contents. It has no GitHub
autodeploy connection and runs as a finite Railway cron service with one replica
and restart NEVER. Configure `PUBLISH_APPLY=0` for full preparation proof before
providing publication credentials.

The controller pins the source workflow and four verifier blobs, verifies the
current protected main through its existing application or frontend signed
receipt, and runs npm/build/rendering as UID10001 without any credential. The
publisher receives only a checked public file tree after that process group is
terminated. Mirror writes compare the previous source revision; recovery must
be persisted on a mounted `/evidence` volume before a public write. GitHub host
keys are pinned; a dedicated write deploy key is limited to seiche-site.

Required service variables: RELEASE_SIGNING_KEY_FINGERPRINT; for publication
also SITE_DEPLOY_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID and
PUBLISH_APPLY=1. No shared/environment-wide credentials. The Cloudflare token
needs only Pages edit on the existing account. A changed workflow/verifier
requires a reviewed controller update.

This adapts publish-static only. It does not replace full evidence collection,
daily articles, Telegram announcements, or package/release attestations.
