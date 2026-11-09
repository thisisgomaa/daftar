# Daftar

**Claude Code on Telegram, with a memory that stays small.**

Daftar (Arabic for *notebook*) turns the official Claude Code CLI into a personal assistant you talk to from Telegram. It runs on your own VPS, Mac, or Linux box, and gives the assistant a long-term memory that stays organised by itself instead of growing until it drowns the context.

```
curl -fsSL https://raw.githubusercontent.com/thisisgomaa/daftar/main/install.sh | sh
```

Then `daftar setup` walks you through the bot token, pairing, and your assistant's persona.

---

## Why

Agents get worse the longer you work with them. Every mistake becomes a lesson, the lessons file grows, and every new session re-reads all of it. In the setup Daftar was extracted from, the assistant was reading about **128 KB** of rules and lessons before its first reply, and the rules that mattered were buried in the middle, which is exactly where long-context models pay the least attention ([Lost in the Middle](https://arxiv.org/abs/2307.03172), [Context Rot](https://research.trychroma.com/context-rot)).

Daftar treats memory like a well-kept notebook:

| | |
|---|---|
| **Map** | A few KB, always in context: how memory works, a handful of core rules, and one index line per topic. |
| **Topics** | One file per topic (writing, server, images, projects…). Opened only when the work touches that topic. |
| **Auto-open** | A Claude Code hook watches your message and the tools being used, and injects the right topic once per session. |
| **One write path** | `daftar kb add <topic> "one line"` saves a lesson, refuses near-duplicates, and updates the map. |
| **Nightly curator** | At 2 AM it merges duplicates, retires outdated lines with a reason, and protects the owner's own decisions. Nothing is ever deleted. |

Same knowledge, about 70% less to read at the start of every session.

## Features

- **Telegram bridge**: owner-only bot with pairing; text, photos, documents and voice notes
- **Voice notes**: transcribed with OpenAI, Groq or ElevenLabs, whichever key you have
- **Interrupt**: send a new message while it works and the running turn is cancelled; pending messages are merged into one turn
- **No lost messages**: anything sent while the machine was off is processed when it comes back
- **Long sessions roll over**: when the context grows past a threshold, the assistant writes a handoff paper (goal, decisions, open items, files, next step) and a fresh session continues from it
- **Chat switching**: `/sessions` lists your last four chats with buttons; `/new` starts fresh with a handoff
- **Files out**: `daftar send <file>` or drop a file into `workspace/outbox/` (optional caption in `<file>.caption`)
- **Images**: `daftar image --prompt "…"` (OpenAI Images, optional)
- **Delegation**: the assistant uses Claude Code's built-in sub-agents for heavy parallel work
- **Background service**: systemd on Linux, launchd on macOS; Windows through WSL
- **Python standard library only**: no pip dependencies

## How it runs Claude

Daftar spawns the official, unmodified `claude` binary as a subprocess in your workspace. You install Claude Code and sign in through Anthropic's own flow. **Daftar never reads, stores or forwards your Claude credentials.**

Anthropic's [terms](https://code.claude.com/docs/en/legal-and-compliance) say consumer-plan sign-in is meant for ordinary use of Claude Code, and that developers building products should use API keys. Running Claude Code through an automation layer may count as third-party usage under those terms. If you want zero ambiguity, set `ANTHROPIC_API_KEY` in Daftar's config and it is passed to Claude Code (API-key mode). You are responsible for your own usage.

Daftar is not affiliated with or endorsed by Anthropic.

## Daftar or the official Telegram channel?

Anthropic ships an official [Telegram channel for Claude Code](https://code.claude.com/docs/en/channels) (research preview). It is first-party and simpler: if you only want to chat with a running session, use it.

Daftar is for people who also want the memory system, voice notes, offline catch-up, interrupt, session rollover with handoff papers, chat switching, and a nightly curator.

## Install

**Prerequisites:** Python 3.10+, [Claude Code](https://code.claude.com) installed and signed in (run `claude` once), and a Telegram bot token from [@BotFather](https://t.me/BotFather).

```bash
# one-liner
curl -fsSL https://raw.githubusercontent.com/thisisgomaa/daftar/main/install.sh | sh

# or from a checkout
git clone https://github.com/thisisgomaa/daftar && cd daftar && ./install.sh
```

Setup asks for: the bot token, a pairing code (message your bot, type the code it sends back), workspace path, reply language, timezone, model, permission mode, a few persona questions, and optional keys for voice and images. Config lives in `~/.daftar/config.env` (mode 600).

Prefer to let Claude Code do it? Open Claude Code and say: *"Read INSTALL.md from github.com/thisisgomaa/daftar and set Daftar up for me."*

Then:

```bash
daftar doctor            # checks everything
daftar service install   # run in the background (or: daftar run)
```

Send `/ping` to your bot.

## Commands

**In Telegram:** `/new` · `/sessions` · `/model [sonnet|opus|haiku]` · `/stop` · `/ping` · `/help`

**On the machine:**

```bash
daftar setup | run | doctor | version
daftar kb add <topic> "lesson" [--by owner|assistant]
daftar kb show <topic> | find "words" | topics | check | map
daftar kb new-topic <slug> "Title" "when to open it"
daftar curator [--dry-run]
daftar send <file> [caption]
daftar image --prompt "…" --out file.png [--ref image]
daftar service install | uninstall | status
```

## Workspace layout

```
daftar-workspace/
  CLAUDE.md              imports the files below
  rules/BASE_RULES.md    sensible defaults (plan before big work, confirm before irreversible steps, …)
  rules/MY_RULES.md      your own standing rules
  persona/PERSONA.md     who the assistant is, written during setup
  knowledge/MAP.md       generated index, always in context
  knowledge/core.md      the few rules that are never optional
  knowledge/topics/*.md  one file per topic
  knowledge/_archive/    everything the curator retired, with reasons
  sessions/              handoff papers
  inbox/  outbox/        files in and out of Telegram
```

## Security

- Only the paired Telegram account can talk to the bot; everyone else is ignored.
- `bypassPermissions` lets the assistant run commands and edit files without asking. Use it only on a machine dedicated to the assistant. Claude Code refuses it as root unless `IS_SANDBOX=1` is set; a normal user account is the better choice.
- Keys live in `~/.daftar/config.env` with mode 600 and are never written to the workspace.

## Roadmap

- Other engines (Codex, OpenAI-compatible APIs)
- Native Windows service
- Smarter curator rules (ideas welcome: open an issue)

## License

MIT
