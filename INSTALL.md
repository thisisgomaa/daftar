# Installing Daftar

## Human quickstart

1. Install [Claude Code](https://code.claude.com) and/or the [Codex CLI](https://github.com/openai/codex), and sign in (`claude` once, and/or `codex login`).
2. Run `curl -fsSL https://raw.githubusercontent.com/thisisgomaa/daftar/main/install.sh | sh`, or clone this repo and run `./install.sh`.
3. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`), paste the token into setup.
4. Message the bot; enter the 6-digit pairing code shown in the terminal.
5. Start the bridge: `daftar run` or `daftar service install`.
6. Send `/ping` — you should get `pong`.

### Add a second bridge (shared memory)

To run Claude and Codex side by side (one bot each, one workspace):

```bash
# after the first bridge is set up:
daftar --profile codex setup
```

Setup asks for the engine (`codex`), a **new** BotFather token, pairing, and whether to share memory with the existing profile. Persona is written to `persona/PERSONA.codex.md`. Then:

```bash
daftar --profile codex doctor
daftar --profile codex service install   # unit: daftar-codex
# or: daftar --profile codex run
```

List bridges: `daftar profiles`. Hand work across them: `daftar delegate --to codex --title "…" --brief "…"`.

Windows: use WSL2, then follow the Linux steps inside the distro.

---

## For Claude Code (execute these steps)

You are installing Daftar for your user. Follow these steps in order. Do not invent credentials. Do not commit secrets. Ask the human when a value must come from Telegram or their accounts.

### 1. Prerequisites

```bash
python3 -c 'import sys; assert sys.version_info >= (3,9), sys.version'
command -v claude || echo 'MISSING_CLAUDE'
command -v codex || echo 'MISSING_CODEX'
```

If `claude` is missing and they want a Claude bridge:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Then have them run `claude` once interactively and complete login. For Codex: install the Codex CLI and run `codex login`. Daftar never performs login or stores credentials.

### 2. Install the app

From the Daftar checkout:

```bash
chmod +x install.sh bin/daftar
./install.sh
```

`install.sh` is idempotent: it checks Python ≥ 3.9, warns if `claude` is missing, copies/syncs the tree to `~/.daftar/app` when appropriate, symlinks `bin/daftar` into `~/.local/bin`, and runs `daftar setup`.

**Manual alternative** (if not using `install.sh`):

```bash
mkdir -p ~/.daftar ~/.local/bin ~/.daftar/logs
ln -sfn "$(pwd)/bin/daftar" ~/.local/bin/daftar
export PATH="$HOME/.local/bin:$PATH"
daftar setup
```

Ensure `~/.local/bin` is on `PATH`.

### 3. `daftar setup` — what you will be asked

Setup is interactive. Prefer keeping existing answers as defaults on re-runs. Use `daftar --profile NAME setup` for additional bridges.

1. **Engine** — detect `claude --version` / `codex --version`; choose `claude` or `codex` for this bridge. Mention `codex login` when relevant.
2. **Telegram bot token** — human creates a bot via BotFather `/newbot` and pastes the token (one bot per bridge). Setup validates with `getMe`.
3. **Pairing** — human sends any message to the bot. Setup shows the sender and sends a 6-digit code to that chat; human types the code in the terminal → `OWNER_ID` saved.
4. **Workspace path** — default `~/daftar-workspace`. If another profile already has a workspace, offer to reuse it ("share memory with …?"). Template files are copied if empty; Claude Code hooks are rewritten with absolute paths. Codex gets `AGENTS.md`.
5. **Language** (default English), **timezone** (detected IANA name; the nightly job runs at `NIGHTLY_HOUR`, default 2 AM, in this zone), **model** (blank = CLI default).
6. **Permission mode** — explain clearly:
   - `bypassPermissions`: full autonomy; **recommended only on a machine dedicated to this assistant** (Codex: `--dangerously-bypass-approvals-and-sandbox`).
   - `acceptEdits` / `default` / non-bypass: safer on shared machines (Codex: `-s workspace-write`).
   - In every mode the paired Telegram account steers an agent running as this user; with bypass it runs any command without asking. In any mode it can read `config.env`. Tell the human; see Security in README.md.
7. **Persona** — assistant name, who the owner is, tone, always/never → writes `persona/PERSONA.md`, or `persona/PERSONA.<profile>.md` when the workspace is shared, plus `rules/MY_RULES.md`.
8. **Optional keys** — `OPENAI_API_KEY`, `GROQ_API_KEY`, `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY` (all optional; explain voice / images / API-key mode).
9. **Optional backup remote** — explain local `DAFTAR_HOME/backups/` archives; offer `BACKUP_RCLONE_REMOTE` (e.g. `gdrive:Daftar`) after `rclone config`. Leave blank to skip.
10. **Connectors** — mention `daftar connect` / `docs/CONNECTORS.md` (MCP on Claude and Codex); OAuth may need one interactive `claude`/`codex` run.
11. **Service** — offer `daftar service install` (named profiles → `daftar-<profile>`).

Config lands in `~/.daftar/config.env` (or `~/.daftar/profiles/<name>/config.env`) with mode `600`. No secrets in the git repo.

### 4. BotFather (human)

1. Open Telegram → `@BotFather`
2. `/newbot` → choose display name and username ending in `bot`
3. Copy the HTTP API token into setup
4. Optional: in BotFather, `/setjoingroups` → Disable (Daftar is a private, one-owner bot)

**Running as root?** Claude Code refuses `bypassPermissions` as root unless `IS_SANDBOX=1` is set. Prefer creating a normal user for the assistant. If the human insists on root on a dedicated machine, setup offers to set `IS_SANDBOX=1`.

**Interactive steps:** pairing needs a human with Telegram open. If you are running setup for them, ask them to send any message to the bot, then relay the 6-digit code they receive.

### 5. Start and verify

```bash
daftar doctor
daftar run
# or: daftar service install
```

In Telegram, send `/ping`. Expect `pong`. Then send a short text question; the reply should come from Claude Code running in the workspace.

### 6. Useful follow-ups

```bash
daftar kb check
daftar kb topics
daftar projects list
daftar backup now
daftar connect list
daftar send /path/to/file.png "optional caption"
daftar curator --dry-run
```

### Optional: backup remote

1. Install [rclone](https://rclone.org/) and run `rclone config` (e.g. a Google Drive remote named `gdrive`).
2. Set `BACKUP_RCLONE_REMOTE=gdrive:Daftar` in config (setup asks, or edit `config.env`).
3. `daftar backup now` creates a local archive, verifies it, uploads with `rclone copyto`, verifies remote size/hash, then prunes old archives (local and remote) beyond `BACKUP_KEEP` (default 7).
4. `config.env` is excluded from backups by default; set `BACKUP_INCLUDE_CONFIG=yes` only if you accept secrets in the archive.

### Optional: connectors (MCP)

See [docs/CONNECTORS.md](docs/CONNECTORS.md). Short version:

```bash
daftar connect add my-api --url https://example.com/mcp
daftar connect add local --command "npx -y some-mcp-server"
daftar connect list
```

If voice notes fail, add a transcription key via re-running `daftar setup` or editing `~/.daftar/config.env`.

### 7. Uninstall service

```bash
daftar service uninstall
```

Remove `~/.daftar` and the workspace directory only if the human asks.
