# Stable questions, dated observations

`questions/pages.json` (under `frontend/public` in Seiche) is the reviewed
editorial source. `build_question_pages.py` renders HTML without network access.
Run `python3 scripts/build_question_pages.py` after editing it; `--check` is a
release check. The source, output, share cards and sitemap belong in one change.

The retained examples are historical and keep their actual observation dates.
`questions.js` loads newer public evidence only when the reader asks, into a
separate panel. It never rewrites the static answer or promotes retrieval time
to source time. Missing, malformed, restricted or mismatched evidence fails
closed. The BTC reader reuses the existing exact-rung and depth/conversion guard.

When the product definition, method, coverage or evidence contract changes,
review the relevant question copy, set its catalog `reviewed_on` to the genuine
editorial date, render, and update the corresponding sitemap-generator dates.
Do not advance these dates just because a daily board, dispatch or regime changes.
The canonical URL stays fixed. Dated letters and current boards remain indexable.

New daily letters continue through their existing publication jobs. The page
links to the letter retained at review and the continually updated archive;
it never relabels an old letter as this morning's publication.

No search, synthetic visitor, external message or ranking guarantee is part of
this mechanism. Search coverage monitoring checks public retrieval separately
from Search Console impressions, citations and attributed product use.
