# Connectors (MCP)

Daftar does not host MCP servers itself. Connectors are registered on the
**engine CLIs** (Claude Code and/or Codex) so every bridge that uses that
engine can call them as tools.

## How it works per engine

### Claude Code

```bash
# HTTP transport
daftar connect add my-api --url https://example.com/mcp --header "Authorization: Bearer …"

# stdio transport
daftar connect add local-tools --command "npx -y @example/mcp-server" --env API_KEY=…
```

Under the hood this runs `claude mcp add --scope user …`. User-scoped
connectors apply to all Claude Code sessions on the machine (including Daftar).

Connectors attached to a **claude.ai account** (for example Gmail, Google Drive,
or Calendar) appear automatically in Claude Code sessions signed in with that
account. You do not need to re-add them with `daftar connect`.

### Codex

```bash
daftar connect add my-api --url https://example.com/mcp
daftar connect add local-tools --command "npx -y @example/mcp-server" --env API_KEY=…
```

This runs `codex mcp add …`. Codex HTTP adds do not accept custom headers;
`daftar connect` warns and skips headers for Codex when you pass `--header`.

## Commands

```bash
daftar connect list
daftar connect add <name> --url URL [--header "K: V"]...
daftar connect add <name> --command "cmd args…" [--env K=V]...
daftar connect remove <name>
```

`add` / `remove` / `list` run against **every** installed engine (`claude` and
`codex` when on PATH). Output shows what ran per engine; secret header and env
values are masked.

## Authorization

Some MCP servers use OAuth. After registering them, you may need **one
interactive** run of `claude` or `codex` in a terminal to complete login. Daftar
never stores those credentials.

## Safety

External writes through connectors (email send, drive upload, calendar create, …)
still need the owner's approval and destination verification — same as any other
external write in the base rules.

## Examples (placeholders only)

```bash
# Hypothetical HTTP MCP
daftar connect add notes --url https://mcp.example.com/v1 \
  --header "Authorization: Bearer YOUR_TOKEN"

# Hypothetical local stdio server
daftar connect add filesystem --command "npx -y @modelcontextprotocol/server-filesystem /path/to/allowed"
```

Do not invent or paste real third-party endpoints unless you own them.
