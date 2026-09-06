Vendored from https://github.com/bitesized/annas-archive-api at commit
`4b3e1d3e3402a3a83cd18c84cdb3ea1b34c5df40` (2026-09-03), not tracked as a git
submodule/remote here since it's a small (~511 line), self-contained service
with no build step.

Ran `npm audit fix` once on top of the upstream `package-lock.json` before
vendoring (fixed a moderate `qs` and a high `undici` transitive advisory,
both pulled in via `express`; 0 vulnerabilities after).

Tested in isolation before deploying (see chat history 2026-09-03): 3
authenticated searches averaged ~3-6s each (vs. Shelfmark's own Anna's
Archive integration, which needs a real headless-Chrome DDoS-guard solve on
every search, ~10-15s minimum and up to 60-120s+ on a cold cache per
Shelfmark's own docs, and was actively rate-limited by Anna's Archive during
testing that same day). An unauthenticated search correctly got rejected
(401) in ~1s, confirming the authenticated-requests-skip-the-challenge
premise this tool is built on. The fast-download proxy also confirmed the
`AA_DONATOR_KEY` already configured for Shelfmark's own direct_download
integration (`shelfmark/config/plugins/download_sources.json`) is a live,
valid donator key.

Caveat worth remembering: this is a very new, single-maintainer project (the
API repo was pushed the same day it got vendored here, 3 stars at the time).
Re-check its GitHub activity/issues if it ever starts behaving oddly before
assuming the bug is on our end.
