# Installing Daftar

## Human quickstart

1. Install [Claude Code](https://code.claude.com) and run `claude` once to log in.
2. Run `curl -fsSL https://raw.githubusercontent.com/thisisgomaa/daftar/main/install.sh | sh`, or clone this repo and run `./install.sh`.
3. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`), paste the token into setup.
4. Message the bot; enter the 6-digit pairing code shown in the terminal.
5. Start the bridge: `daftar run` or `daftar service install`.
6. Send `/ping` — you should get `pong`.

Windows: use WSL2, then follow the Linux steps inside the distro.

---

## For Claude Code (execute these steps)

You are installing Daftar for your user. Follow these steps in order. Do not invent credentials. Do not commit secrets. Ask the human when a value must come from Telegram or their accounts.

### 1. Prerequisites

```bash
python3 -c 'import sys; assert sys.version_info >= (3,10), sys.version'
command -v claude || echo 'MISSING_CLAUDE'
```

If `claude` is missing, tell the human to install it:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Then have them run `claude` once interactively and complete login. Daftar never performs Claude login for them.

### 2. Install the app

From the Daftar checkout:

```bash
chmod +x install.sh bin/daftar
./install.sh
```

`install.sh` is idempotent: it checks Python ≥ 3.10, warns if `claude` is missing, copies/syncs the tree to `~/.daftar/app` when appropriate, symlinks `bin/daftar` into `~/.local/bin`, and runs `daftar setup`.

**Manual alternative** (if not using `install.sh`):

```bash
mkdir -p ~/.daftar ~/.local/bin ~/.daftar/logs
ln -sfn "$(pwd)/bin/daftar" ~/.local/bin/daftar
export PATH="$HOME/.local/bin:$PATH"
daftar setup
```

Ensure `~/.local/bin` is on `PATH`.

### 3. `daftar setup` — what you will be asked

Setup is interactive. Prefer keeping existing answers as defaults on re-runs.

1. **Claude check** — `claude --version`.
2. **Telegram bot token** — human creates a bot via BotFather `/newbot` and pastes the token. Setup validates with `getMe`.
3. **Pairing** — human sends any message to the bot. Setup shows the sender and sends a 6-digit code to that chat; human types the code in the terminal → `OWNER_ID` saved.
4. **Workspace path** — default `~/daftar-workspace`. Template files are copied if empty; Claude Code hooks are rewritten with absolute paths.
5. **Language** (default English), **timezone** (detected), **model** (blank = CLI default).
6. **Permission mode** — explain clearly:
   - `bypassPermissions`: full autonomy; **recommended only on a machine dedicated to this assistant**.
   - `acceptEdits` / `default`: safer on shared machines.
7. **Persona** — assistant name, who the owner is, tone, always/never → writes `persona/PERSONA.md` and `rules/MY_RULES.md`.
8. **Optional keys** — `OPENAI_API_KEY`, `GROQ_API_KEY`, `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY` (all optional; explain voice / images / API-key mode).
9. **Service** — offer `daftar service install`.

Config lands in `~/.daftar/config.env` with mode `600`. No secrets in the git repo.

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
daftar send /path/to/file.png "optional caption"
daftar curator --dry-run
```

If voice notes fail, add a transcription key via re-running `daftar setup` or editing `~/.daftar/config.env`.

### 7. Uninstall service

```bash
daftar service uninstall
```

Remove `~/.daftar` and the workspace directory only if the human asks.
