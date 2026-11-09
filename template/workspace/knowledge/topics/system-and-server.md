# System and server
> Open when: Services, cron, restarts, tokens, backups, or destructive server ops
> New lesson: `daftar kb add system-and-server "one-line lesson" --by <name>`

## Owner guidance

## Lessons
- 2026-11-09 [daftar] `pkill -f <pattern>` inside the same shell command kills the shell itself if the pattern appears in the command; kill by PID or use a pattern absent from the command line.
- 2026-11-09 [daftar] systemd `ProtectHome=read-only` or `ProtectSystem=strict` silently breaks writes; test with `systemd-run` using the same properties and a real write, not by running the command by hand.
- 2026-11-09 [daftar] A service that calls an OAuth-based CLI needs that CLI's config directory writable, because token refresh writes there.
- 2026-11-09 [daftar] "active" in systemd does not mean serving; probe the local endpoint, since a master process with no workers accepts connections and never replies.
- 2026-11-09 [daftar] Files you write for a service running as another user may be unreadable to it; fix ownership and load the real page, not just the status code.
- 2026-11-09 [daftar] Restarting the process that hosts your own session kills your turn; send the reply first, then schedule the restart (e.g. `systemd-run --on-active=30`).
- 2026-11-09 [daftar] After a usage or quota limit resets, rerun the jobs that failed silently while it was exhausted.
- 2026-11-09 [daftar] Test a token's real scope on the endpoint you need; never infer permissions from its variable name.
