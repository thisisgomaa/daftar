# Security

## Supported versions

Only `main` (the latest release) gets fixes.

## Threat model

Daftar connects a Telegram account to a coding agent on your machine. By design:

- A message from the paired Telegram account directs an agent running as the service user. With `bypassPermissions` (the default; Codex: `--dangerously-bypass-approvals-and-sandbox`) it runs any command without asking.
- The assistant can read anything that user can, including `~/.daftar/config.env` (bot token and keys).
- API keys from `config.env` are exported to the `claude` / `codex` child process and the commands it runs.
- Content you forward to the bot (files, photos, pasted text) and pages it reads are untrusted and can carry prompt injection.
- A system unit installed as root runs as root. Use a dedicated, unprivileged user.

In scope: anything that lets someone other than the paired account reach the agent, leaks `config.env` or keys outside the machine, or breaks the file-name and path handling of the inbox, outbox, backups or restore.

Out of scope: what the paired account asks the agent to do, and prompt injection in content the owner chose to forward (mitigate with a non-bypass permission mode).

## Reporting a vulnerability

Please do not open a public issue. Either:

- open a private security advisory on GitHub (Security tab → "Report a vulnerability"), or
- email the maintainer at the address in the commit history.

Include steps to reproduce and the version (`daftar version`). Response is best effort.
