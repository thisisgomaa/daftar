# Coding
> Open when: Writing, reviewing, or debugging code and repositories
> New lesson: `daftar kb add coding "one-line lesson" --by <name>`

## Owner guidance

## Lessons
- 2026-11-09 [daftar] Run `git fetch` and confirm your branch is current before editing, not after the code is written.
- 2026-11-09 [daftar] Deploy only from committed, pushed code; a manual production deploy from a local copy is either stale or gets wiped by the next git deploy.
- 2026-11-09 [daftar] Test web changes by loading the real page and reading the browser console; CSP and CORS failures never show up in builds or curl.
- 2026-11-09 [daftar] Mark an inbound message handled only after the reply succeeds, and persist a "sending" state first so a crash cannot cause a duplicate send.
- 2026-11-09 [daftar] Webhooks can fire more than once for the same person or event; make handlers idempotent.
- 2026-11-09 [daftar] Render, build, and test with the same tool version you developed against; version drift silently changes output.
