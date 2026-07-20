# Cloudflare Pages path for seiche.info

seiche.info is served by GitHub Pages (seiche-site repo) behind a Cloudflare
proxy — GitHub Pages sends no custom headers, so the security headers in
`frontend/public/_headers` only go live once the domain serves from
Cloudflare Pages. The publish workflow already deploys `frontend/dist` to a
Pages project named `seiche` (optional step); two one-time manual steps make
that real:

1. **Mint a token and store it as a repo secret.** Cloudflare dashboard → My
   Profile → API Tokens → create a token with **Pages: Edit** (account scope)
   and **Zone: Read** (for the domain attach below). Then:

   ```sh
   gh secret set CLOUDFLARE_API_TOKEN -R beepboop2025/seiche
   ```

   (`CLOUDFLARE_ACCOUNT_ID` is already set.) From the next publish on, the
   Pages deploy step runs for real and stops warning.

2. **Attach the custom domain to the Pages project.** Locally, with the token
   and account id in the environment:

   ```sh
   CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… \
     ops/cloudflare/attach_pages_domain.sh
   ```

   Idempotent; re-run to watch validation status. Because the seiche.info
   zone is already on Cloudflare and proxied, validation is quick and no DNS
   changes are needed.

Afterwards `curl -sI https://seiche.info/` should show HSTS, CSP and friends.
The GitHub Pages mirror stays deployed as a fallback — it simply never sends
custom headers.
