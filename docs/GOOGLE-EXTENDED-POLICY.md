# Reviewed Google-Extended permission

The owner authorized Google-Extended only on reviewed product explanations on
6 October 2026, with data and archives excluded. Google couples Gemini/Vertex
grounding and model training in this control. Ordinary Google Search access is
separate: <https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers#google-extended>.

The three current-source pages in `google-extended-reviewed-pages.json` contain
Seiche-authored workflow explanations. They describe money-market research,
capital-market transmission and China economic-evidence boundaries without
embedding source observations, archived letters, third-party extracts or live
data responses. References and sample API commands are links/instructions, not
a permission grant over their targets. The shared authority-actions script
provides citation/share controls and does not inject a market-data response.

The robot group blocks `/` and allows only each exact canonical path with a `$`
anchor. The homepage, APIs, data, feeds, dispatches, articles, catalogs, future
guides, nested downloads and query variants stay excluded from Google-Extended.
Terms and the generated LLM index state the same narrow permission. Other
training crawlers retain their exclusions. Robots compliance is voluntary and
does not establish actual model use or endorsement.

## Product updates

The normal backend test suite compares the three complete page files and their
shared script with the retained review hashes. A changed file requires a new
content review and receipt update before CI passes, or removal of its allowance.
Do not regenerate these hashes automatically during builds. If a future page
adds third-party observations or dynamic data, remove that exact allowance
before publication. New explanation pages remain blocked until separately
reviewed. No crawler permission is inferred from sitemap membership.

## Publication

Use the existing signed application/static publication gates. This change also
updates the generated LLM-index preamble, so a frontend-only receipt is not
eligible. The discovery Worker is a transport proxy and must not replace Pages
content or bypass a held publisher. A merged PR is not proof of live permission.
After a qualified release, compare public robots and terms to the staged source,
check the three allowed paths and representative data/archive paths, and retain
the release and readback receipts. The fleet monitor separately observes live
Google-Extended policy; it does not grant permission or assert Gemini citations.
