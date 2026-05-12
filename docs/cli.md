# CLI Reference

The product command is `ag <command>`. From a fresh clone, use the repo-local launcher `./ag <command>` before installing.

```bash
ag <command>
./ag <command>
```

After `./setup-ag.sh`, `ag`, `ag-cli`, and `antigravity-cli` work when `~/.local/bin` is on `PATH`. Examples below use `./ag` so they also work immediately after cloning; replace it with `ag` after setup.

## Health And Models

```bash
./ag doctor
./ag doctor --json
./ag models
./ag models --json
```

`ag models` reads the same `GetUserStatus.userStatus.cascadeModelConfigData.clientModelConfigs` payload used by Antigravity's UI model picker.

## Chat

```bash
./ag ask "hello"
./ag ask "hello" --async

./ag chat send "continue" --session <session_id>
./ag chat resume --session <session_id> "continue"
./ag chat start "new session"
./ag chat start "new session" --async
./ag chat stream "stream a reply"
```

Useful flags:

```bash
--model 1037
--model-label "Gemini 3 Flash"
--attachment /absolute/path/to/file
--timeout 60
--idle-seconds 1.5
--poll 0.5
--json
```

Streaming defaults to answer text on stdout and session metadata on stderr. Add `--json` to get event JSON lines from the underlying store.

## Sessions

```bash
./ag sessions list
./ag sessions list --json
./ag sessions show <session_id>
./ag sessions files <session_id>
./ag sessions messages <session_id>
./ag sessions messages <session_id> --refresh
```

Session data comes from:

- `~/.gemini/antigravity/conversations`
- `~/.gemini/antigravity/brain`
- `.cache`

Uploaded files are stored under:

```text
.cache/<session_id>/uploads/
```

## UI

```bash
./ag ui serve --open
```

`ag ui serve` opens the local chat composer UI from `ui.py`, including the message box, model picker, attachment picker, and send buttons.

## Attachments And Cache

```bash
./ag attachments bytes /absolute/path/to/file
./ag cache warm --limit 20
./ag cache warm --limit 20 --json
```

`attachments bytes` always prints JSON because it returns base64 file contents.

## Runtime

```bash
./ag runtime models
./ag runtime models --fallback
./ag runtime send "hello"
./ag runtime resume --session <session_id> "continue"
```

Runtime commands use the headless Antigravity language-server transport directly.
