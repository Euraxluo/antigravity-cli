# Troubleshooting

Start with `ag` after setup, or `./ag` from a fresh clone:

```bash
./ag doctor
./ag doctor --json
```

## `antigravity_process` Fails

Antigravity is not running, or the language server process is not visible.

Fix:

```bash
open -a Antigravity
./ag doctor
```

If multiple workspaces are open, set a hint:

```bash
AG_RUNTIME_WORKSPACE_HINT=antigravity-cli ./ag doctor
```

## `language_server` Or `get_user_status` Fails

The CLI found a process but could not discover the ConnectRPC endpoint or call `GetUserStatus`.

Try:

```bash
./ag runtime models --fallback
./ag models --launch-runtime
./ag doctor --json
```

If the failure persists, restart Antigravity and rerun `doctor`.

## Missing `typer`

If you see a Python dependency error:

```bash
./setup-ag.sh
```

or make sure `uv` is installed and rerun the command:

```bash
uv --version
./ag sessions list
```

You can also force a known Python:

```bash
ANTIGRAVITY_CLI_PYTHON=/path/to/.venv/bin/python ./ag sessions list
```

## Models Look Wrong

`ag models` prefers live Antigravity UI config. If Antigravity is not available, it falls back to the current known list.

Override temporarily:

```bash
ANTIGRAVITY_MODELS_JSON='[{"id":1037,"label":"Gemini 3.1 Pro (High)","default":true}]' ./ag models
```

Override persistently:

```text
~/.config/antigravity-cli/models.json
```

## Sessions List Is Empty

The CLI reads Antigravity's local session stores:

```text
~/.gemini/antigravity/conversations
~/.gemini/antigravity/brain
.cache
```

Open Antigravity and create or use a conversation, then rerun:

```bash
./ag sessions list
```

## Cache Or Upload Checks Fail

The CLI writes local cache under:

```text
.cache
```

Fix permissions or remove stale files:

```bash
mkdir -p .cache
chmod u+rwX .cache
```
