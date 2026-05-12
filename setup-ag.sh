#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  UV="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV="$HOME/.local/bin/uv"
elif [ -x "$HOME/.cargo/bin/uv" ]; then
  UV="$HOME/.cargo/bin/uv"
else
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  if [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
  elif [ -x "$HOME/.cargo/bin/uv" ]; then
    UV="$HOME/.cargo/bin/uv"
  else
    echo "error: uv install completed but uv was not found" >&2
    exit 1
  fi
fi

if [ ! -x ".venv/bin/python" ]; then
  "$UV" venv .venv --python 3.11
fi

"$UV" pip install -e .
chmod +x "$ROOT/ag"

BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$ROOT/ag" "$BIN_DIR/ag"
ln -sf "$ROOT/ag" "$BIN_DIR/ag-cli"
ln -sf "$ROOT/ag" "$BIN_DIR/antigravity-cli"

echo "Antigravity CLI is ready."
echo "Repo-local: $ROOT/ag doctor"
echo "Shell command: ag doctor"
echo "Aliases: ag-cli, antigravity-cli"
echo "Optional PATH entry: export PATH=\"\$HOME/.local/bin:\$PATH\""
