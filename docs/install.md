# Install And Bootstrap

Antigravity CLI is designed to work from a fresh clone. The repo-local command is `./ag`; after setup, the installed product commands are `ag`, `ag-cli`, and `antigravity-cli`.

## Zero-Setup Path

```bash
./ag doctor
./ag models
./ag sessions list
```

The launcher bootstraps itself. If `.venv` is missing it looks for `uv`, installs `uv` with the official installer when needed, creates `.venv`, and installs this package in editable mode before running the command.

## Explicit Setup

```bash
./setup-ag.sh
```

This does four things:

1. Finds or installs `uv`.
2. Creates `.venv` with Python 3.11.
3. Runs `uv pip install -e .`.
4. Symlinks `./ag` to `~/.local/bin/ag`, `~/.local/bin/ag-cli`, and `~/.local/bin/antigravity-cli`.

After setup:

```bash
export PATH="$HOME/.local/bin:$PATH"
ag doctor
ag-cli models
antigravity-cli sessions list
```

## Environment Overrides

Use these only when you need to force a specific runtime:

```bash
ANTIGRAVITY_CLI_PYTHON=/path/to/python ag sessions list
AG_PYTHON=/path/to/python ag sessions list
AG_NO_BOOTSTRAP=1 ag sessions list
AG_NO_UV_INSTALL=1 ag sessions list
```

Model overrides:

```bash
ANTIGRAVITY_MODELS_JSON='[{"id":1037,"label":"Gemini 3.1 Pro (High)","default":true}]' ag models
```

or:

```text
~/.config/antigravity-cli/models.json
```

## Requirements

- macOS with Antigravity installed for live runtime features.
- Python 3.11+ recommended.
- `uv` is used for automatic dependency bootstrap; `./ag` can install it when missing unless `AG_NO_UV_INSTALL=1` is set.

Without Antigravity running, static/help commands still work, but `doctor`, live model discovery, session refresh, runtime send, and UI control may report unavailable services.
