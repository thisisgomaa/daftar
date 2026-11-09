# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-11-09

### Added

- Codex CLI engine (`ENGINE=codex`), with `AGENTS.md` generated from rules, persona and map.
- Per-engine profiles (`--profile NAME`) that can share one workspace and memory.
- Delegation between bridges (`daftar delegate`, `daftar jobs`).
- Nightly project summaries into `projects/<slug>/STATUS.md` + `LOG.md`; `/projects`.
- Verified backups (`daftar backup`), optional rclone remote, prune only after verify.
- MCP connectors for Claude Code and Codex (`daftar connect`).
- `SECURITY.md`, this changelog, and CI on Linux and macOS.

### Fixed

- Inbox file names are sanitised; uploads can no longer escape `inbox/`.
- Prompts are passed on stdin, so large prompts or prompts starting with `-` work.
- Edited Telegram messages no longer re-run as a new turn.
- systemd unit gets a literal `PATH`; `systemctl` failures are reported.
- `daftar send <file> "caption"` caption is delivered.
- Nightly hour follows the configured `TIMEZONE`.

### Changed

- Minimum Python is 3.9.

## [0.1.0] — 2026-11-09

### Added

- Initial release: Telegram bridge for Claude Code with a self-organising memory
  (map, topics, `daftar kb`, nightly curator).
