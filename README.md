# Daftar

**Claude Code or Codex on Telegram, with a memory that stays small.**

Daftar (Arabic for *notebook*) turns the official Claude Code CLI — or OpenAI's Codex CLI — into a personal assistant you talk to from Telegram. It runs on your own VPS, Mac, or Linux box, and gives the assistant a long-term memory that stays organised by itself instead of growing until it drowns the context.

```
curl -fsSL https://raw.githubusercontent.com/thisisgomaa/daftar/main/install.sh | sh
```

Then `daftar setup` walks you through the engine, bot token, pairing, and your assistant's persona.

---

## Why

Agents get worse the longer you work with them. Every mistake becomes a lesson, the lessons file grows, and every new session re-reads all of it. In the setup Daftar was extracted from, the assistant was reading about **128 KB** of rules and lessons before its first reply, and the rules that mattered were buried in the middle, which is exactly where long-context models pay the least attention ([Lost in the Middle](https://arxiv.org/abs/2307.03172), [Context Rot](https://research.trychroma.com/context-rot)).

Daftar treats memory like a well-kept notebook:

| | |
|---|---|
| **Map** | A few KB, always in context: how memory works, a handful of core rules, and one index line per topic. |
| **Topics** | One file per topic (writing, server, images, projects…). Opened only when the work touches that topic. |
| **Auto-open** | A Claude Code hook (or Codex prompt injection) watches your message and injects the right topic once per session. |
| **One write path** | `daftar kb add <topic> "one line"` saves a lesson, refuses near-duplicates, and updates the map. |
| **Nightly curator** | Once a night (2 AM in your `TIMEZONE` by default, set by `NIGHTLY_HOUR`) it merges duplicates, retires outdated lines with a reason, and protects the owner's own decisions. Retired lines go to `_archive/`, never deleted. |

In the setup Daftar was extracted from, this cut what the assistant reads at session start by about 70%; your numbers will depend on your notes.

## Run it on Claude Code, Codex, or both

Each bridge is one engine, chosen at setup (`ENGINE=claude` or `ENGINE=codex`). You can run **two bots that share one workspace** — for example Claude for chat and Codex for images — so they share the knowledge base while keeping separate persona files, Telegram bots, and session state.

- `daftar setup` — first bridge (default home `~/.daftar/`)
- `daftar --profile codex setup` — second bridge under `~/.daftar/profiles/codex/`
- Setup offers to reuse an existing workspace ("share memory with …?")
- Shared workspaces write `persona/PERSONA.<profile>.md` so each bot keeps its own name and voice
- Hand work across bridges with `daftar delegate`; the requester's bridge delivers the result to you

Codex reads `AGENTS.md` (regenerated from rules + persona + MAP). Claude Code keeps using `CLAUDE.md` with `@imports`.

## Features

- **Telegram bridge**: owner-only bot with pairing; text, photos, documents and voice notes
- **Engines**: Claude Code or Codex CLI (one engine per bridge)
- **Profiles**: several bridges, one optional shared memory
- **Delegation**: hand a job to another bridge; result comes back for delivery
- **Voice notes**: transcribed with OpenAI, Groq or ElevenLabs, whichever key you have
- **Interrupt**: send a new message while it works and the running turn is cancelled; pending messages are merged into one turn
- **Offline catch-up**: Telegram holds undelivered messages for about 24 hours, and the bridge resumes from its saved offset, so messages sent while it was down are picked up when it starts, handled like messages sent mid-turn (see Interrupt). Updates are acknowledged as they arrive, so a turn that is running, and messages queued behind it in memory, are lost if the bridge stops mid-turn: resend them.
- **Long sessions roll over**: when the context grows past a threshold, the assistant writes a handoff paper (goal, decisions, open items, files, next step) and a fresh session continues from it
- **Chat switching**: `/sessions` lists your last four chats with buttons; `/new` starts fresh with a handoff
- **Files out**: `daftar send <file>` or drop a file into `workspace/outbox/` (optional caption in `<file>.caption`)
- **Images**: Codex built-in image tool (auto-delivered) or `daftar image --prompt "…"` (OpenAI Images)
- **Project summaries**: nightly digest of the day's session transcripts into `projects/<slug>/STATUS.md` + `LOG.md`
- **Backups**: verified local tar.gz (optional rclone remote); prune only after verify
- **Connectors**: register MCP servers on Claude Code and/or Codex (`daftar connect`)
- **Background service**: systemd on Linux, launchd on macOS; Windows through WSL
- **Python standard library only**: no pip dependencies

## How it runs Claude

Daftar spawns the official, unmodified `claude` binary as a subprocess in your workspace. You install Claude Code and sign in through Anthropic's own flow. **Daftar never reads, stores or forwards your Claude credentials.**

Anthropic's [terms](https://code.claude.com/docs/en/legal-and-compliance) say consumer-plan sign-in is meant for ordinary use of Claude Code, and that developers building products should use API keys. Running Claude Code through an automation layer may count as third-party usage under those terms. If you want zero ambiguity, set `ANTHROPIC_API_KEY` in Daftar's config and it is passed to Claude Code (API-key mode). You are responsible for your own usage.

Daftar is not affiliated with or endorsed by Anthropic.

## How it runs Codex

Codex bridges run the official `codex` CLI with your own login. Run `codex login` on the machine (ChatGPT account or API key). **Daftar never touches Codex credentials.** On auth errors the bridge replies: `Codex is not logged in. Run \`codex login\` on this machine.`

Daftar is not affiliated with or endorsed by OpenAI.

## Daftar or the official Telegram channel?

Anthropic ships an official [Telegram channel for Claude Code](https://code.claude.com/docs/en/channels) (research preview). It is first-party and simpler: if you only want to chat with a running session, use it.

Daftar is for people who also want the memory system, voice notes, offline catch-up, interrupt, session rollover with handoff papers, chat switching, multi-engine profiles, and a nightly curator.

## Install

**Prerequisites:** Python 3.9+, [Claude Code](https://code.claude.com) and/or [Codex CLI](https://github.com/openai/codex) installed and signed in, and a Telegram bot token from [@BotFather](https://t.me/BotFather) (one bot per bridge).

```bash
# one-liner
curl -fsSL https://raw.githubusercontent.com/thisisgomaa/daftar/main/install.sh | sh

# or from a checkout
git clone https://github.com/thisisgomaa/daftar && cd daftar && ./install.sh
```

Setup asks for: the engine, the bot token, a pairing code, workspace path (with optional memory sharing), reply language, timezone, model, permission mode, a few persona questions, and optional keys for voice and images. Config lives in `~/.daftar/config.env` (mode 600), or under `~/.daftar/profiles/<name>/` for named profiles.

Prefer to let Claude Code do it? Open Claude Code and say: *"Read INSTALL.md from github.com/thisisgomaa/daftar and set Daftar up for me."*

Then:

```bash
daftar doctor            # checks everything
daftar service install   # run in the background (or: daftar run)
```

Send `/ping` to your bot.

## Commands

**In Telegram:** `/new` · `/sessions` · `/projects` · `/model [sonnet|opus|haiku|default]` · `/stop` · `/ping` · `/help`. Any other `/word` is passed to the assistant as text.

**On the machine:**

```bash
daftar [--profile NAME] setup | run | doctor | version
daftar profiles
daftar delegate --to <profile> --title "…" (--brief "…" | --brief-file F) [--attach PATH]...
daftar jobs
daftar kb add <topic> "lesson" [--by NAME] [--force]
daftar kb show <topic> | find "words" | topics | check | map
daftar kb new-topic <slug> "Title" "when to open it"
daftar projects list | show <slug> | summarize [--date YYYY-MM-DD] [--dry-run]
daftar backup now | list | restore <archive> --to <dir> [--force]
daftar connect list
daftar connect add <name> --url URL [--header "K: V"]...
daftar connect add <name> --command "cmd args" [--env KEY=VALUE]...
daftar connect remove <name>
daftar curator [--dry-run] [--full] [--topic SLUG]
daftar send <file> [caption]
daftar image --prompt "…" --out file.png [--ref image]... [--size 1024x1024]
daftar service [install | uninstall | status]
```

`--profile NAME` (or env `DAFTAR_PROFILE`) works with every command and puts config/state/logs in `DAFTAR_HOME/profiles/NAME/`. No flag keeps the existing default home. `kb add --by owner` marks a line the curator must never merge or retire.

## Workspace layout

```
daftar-workspace/
  CLAUDE.md              imports the files below (Claude)
  AGENTS.md              generated for Codex from the same sources
  rules/BASE_RULES.md    sensible defaults (plan before big work, confirm before irreversible steps, …)
  rules/MY_RULES.md      your own standing rules
  persona/PERSONA.md     who the assistant is (or PERSONA.<profile>.md when sharing)
  knowledge/MAP.md       generated index, always in context
  knowledge/core.md      the few rules that are never optional
  knowledge/topics/*.md  one file per topic
  knowledge/_archive/    everything the curator retired, with reasons
  projects/              INDEX.md + <slug>/{STATUS,LOG}.md (nightly summaries)
  sessions/              handoff papers
  sessions/transcripts/  append-only turn logs (source for summaries)
  delegations/           cross-bridge job workspaces
  inbox/  outbox/        files in and out of Telegram
```

## Security

Daftar gives a chat account control of a machine. Know what that means:

- **A message is a shell.** Only the paired Telegram account is answered, but whoever controls it directs an agent running as the service user. With `bypassPermissions` (the default; Codex: `--dangerously-bypass-approvals-and-sandbox`) that agent runs any command without asking. Protect the Telegram account (two-step verification).
- **The assistant can read anything that user can**, including `~/.daftar/config.env` with the bot token and keys.
- **Keys reach the engine.** API keys from `config.env` are exported to the environment of the `claude` / `codex` child process and the commands it runs.
- **Forwarded content is untrusted.** Files, photos, pasted text and pages the assistant reads can carry prompt injection that steers it into running commands.
- **Root means root.** `daftar service install` run as root writes a system unit that runs as root. Use a dedicated, unprivileged user (Claude Code also refuses `bypassPermissions` as root unless `IS_SANDBOX=1`).
- On a shared machine use `acceptEdits` or `default` (Codex: `-s workspace-write`).
- `config.env` (or a profile's) is mode 600, never written to the workspace, and left out of backups unless `BACKUP_INCLUDE_CONFIG=yes`.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Tests

From the repo root (no network, accounts, or real `claude` / `codex` needed; the tests use fakes):

```bash
python3 -m unittest discover -s tests
```

Do not add `-I`: isolated mode drops the repo root from `sys.path` and the `daftar` imports fail.

## Roadmap

- Native Windows service
- Smarter curator rules (ideas welcome: open an issue)

## License

MIT, see [LICENSE](LICENSE). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

Daftar is not affiliated with or endorsed by Anthropic or OpenAI.
