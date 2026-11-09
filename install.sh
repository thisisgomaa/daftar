#!/bin/sh
# Daftar installer — POSIX sh, idempotent.
set -eu

need_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install Python >= 3.10 first." >&2
    exit 1
  fi
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python >= 3.10 required." >&2
    exit 1
  fi
}

need_engines() {
  if command -v claude >/dev/null 2>&1; then
    :
  else
    echo "Claude Code CLI (\`claude\`) not found on PATH (needed for a Claude bridge)."
    echo "Install it with:"
    echo "  curl -fsSL https://claude.ai/install.sh | bash"
  fi
  if command -v codex >/dev/null 2>&1; then
    :
  else
    echo "Codex CLI (\`codex\`) not found on PATH (optional — for a Codex bridge)."
    echo "Install Codex, then run: codex login"
  fi
  echo "Then run: daftar setup   (or: daftar --profile NAME setup)"
}

resolve_src() {
  # Prefer the directory containing this script when it looks like a checkout
  HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
  if [ -d "$HERE/daftar" ] && [ -f "$HERE/bin/daftar" ]; then
    echo "$HERE"
    return 0
  fi
  return 1
}

DAFTAR_HOME="${DAFTAR_HOME:-$HOME/.daftar}"
APP_DIR="$DAFTAR_HOME/app"
BIN_DIR="${DAFTAR_BIN_DIR:-$HOME/.local/bin}"

need_python
need_engines || true

mkdir -p "$DAFTAR_HOME" "$BIN_DIR" "$DAFTAR_HOME/logs"

if SRC=$(resolve_src); then
  echo "Using checkout at $SRC"
  # If not already living under ~/.daftar/app, sync a copy for the service
  if [ "$SRC" != "$APP_DIR" ]; then
    mkdir -p "$APP_DIR"
    # Copy tree (keep .git if present — optional)
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude '.daftar-test*' "$SRC"/ "$APP_DIR"/
    else
      # Portable fallback
      rm -rf "$APP_DIR"
      mkdir -p "$APP_DIR"
      cp -R "$SRC"/. "$APP_DIR"/
      rm -rf "$APP_DIR"/.git 2>/dev/null || true
    fi
    SRC="$APP_DIR"
  fi
else
  # Piped install (curl ... | sh): fetch the repo into ~/.daftar/app
  REPO="${DAFTAR_REPO:-https://github.com/thisisgomaa/daftar}"
  if [ -d "$APP_DIR/.git" ] && command -v git >/dev/null 2>&1; then
    echo "Updating $APP_DIR"
    git -C "$APP_DIR" pull --ff-only
  elif command -v git >/dev/null 2>&1; then
    echo "Cloning $REPO into $APP_DIR"
    rm -r "$APP_DIR" 2>/dev/null || true
    git clone --depth 1 "$REPO" "$APP_DIR"
  else
    echo "Downloading $REPO (no git found)"
    TMP=$(mktemp -d)
    curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$TMP"
    rm -r "$APP_DIR" 2>/dev/null || true
    mv "$TMP"/daftar-main "$APP_DIR"
    rmdir "$TMP" 2>/dev/null || true
  fi
  SRC="$APP_DIR"
fi

chmod +x "$SRC/bin/daftar"
ln -sfn "$SRC/bin/daftar" "$BIN_DIR/daftar"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo "Note: add $BIN_DIR to PATH (e.g. in ~/.profile):"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

export DAFTAR_HOME
echo "Running daftar setup…"
# When piped from curl, stdin is the script itself; read answers from the terminal.
if [ -t 0 ]; then
  "$BIN_DIR/daftar" setup
elif (: < /dev/tty) 2>/dev/null; then
  "$BIN_DIR/daftar" setup < /dev/tty
else
  echo "No terminal available. Run: daftar setup"
fi
