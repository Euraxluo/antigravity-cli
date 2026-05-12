#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


def repo_root() -> Path:
    return Path(__file__).resolve().parent


REPO_ROOT = repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def find_uv() -> Optional[str]:
    for candidate in (
        shutil.which("uv"),
        str(Path.home() / ".local" / "bin" / "uv"),
        str(Path.home() / ".cargo" / "bin" / "uv"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def ensure_local_environment(module: str) -> Optional[Path]:
    if os.environ.get("AG_NO_BOOTSTRAP"):
        return None
    uv = find_uv()
    if not uv:
        return None

    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    try:
        if not venv_python.exists():
            sys.stderr.write("[ag bootstrap] creating .venv with uv\n")
            sys.stderr.flush()
            subprocess.run([uv, "venv", str(REPO_ROOT / ".venv"), "--python", "3.11"], cwd=str(REPO_ROOT), check=True)

        probe = subprocess.run([str(venv_python), "-c", f"import {module}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode != 0:
            sys.stderr.write(f"[ag bootstrap] installing dependency for `{module}`\n")
            sys.stderr.flush()
            if (REPO_ROOT / "pyproject.toml").exists():
                subprocess.run([uv, "pip", "install", "-e", "."], cwd=str(REPO_ROOT), check=True)
            else:
                subprocess.run([uv, "pip", "install", "--python", str(venv_python), "typer>=0.12"], cwd=str(REPO_ROOT), check=True)

        probe = subprocess.run([str(venv_python), "-c", f"import {module}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return venv_python
    except Exception as exc:
        sys.stderr.write(f"[ag bootstrap] failed: {exc}\n")
        sys.stderr.flush()
    return None


def python_with_module(module: str) -> str:
    override = os.environ.get("ANTIGRAVITY_CLI_PYTHON") or os.environ.get("AG_PYTHON")
    candidates = [
        Path(override).expanduser() if override else None,
        REPO_ROOT / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate is None or not candidate.exists():
            continue
        proc = subprocess.run(
            [str(candidate), "-c", f"import {module}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            return str(candidate)
    bootstrapped = ensure_local_environment(module)
    if bootstrapped:
        return str(bootstrapped)
    return sys.executable


def store_cmd() -> list[str]:
    python = python_with_module("typer")
    probe = subprocess.run([python, "-c", "import typer"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode != 0:
        raise RuntimeError(
            "Python dependency `typer` is missing. Run `./setup-ag.sh`, "
            "or install uv and rerun this command for automatic bootstrap."
        )
    return [python, str(REPO_ROOT / "store.py")]


def runtime_cmd() -> list[str]:
    return [sys.executable, str(REPO_ROOT / "runtime_cli" / "ag_runtime.py")]


def session_ui_cmd() -> list[str]:
    return [python_with_module("typer"), str(REPO_ROOT / "ui.py")]


def run_passthrough(command: Sequence[str]) -> int:
    proc = subprocess.run(list(command))
    return int(proc.returncode)


def run_capture(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(message: str, *, json_output: bool = False, code: int = 1) -> int:
    if json_output:
        print_json({"ok": False, "error": message})
    else:
        sys.stderr.write(f"error: {message}\n")
    return code


def load_models(*, launch_runtime: bool = False) -> list[dict[str, Any]]:
    from runtime_cli.model_registry import load_model_options

    return load_model_options(dynamic=True, launch=launch_runtime)


def resolve_model_id(args: argparse.Namespace) -> Optional[int]:
    model = getattr(args, "model", None)
    label = getattr(args, "model_label", None)
    launch_runtime = bool(getattr(args, "launch_runtime", False))
    if model is not None:
        return int(model)
    if not label:
        return None

    needle = label.strip().lower()
    models = load_models(launch_runtime=launch_runtime)
    for item in models:
        if str(item.get("label") or "").lower() == needle:
            return int(item["id"])
    for item in models:
        if needle in str(item.get("label") or "").lower():
            return int(item["id"])
    raise RuntimeError(f"model label not found: {label}")


def add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", type=int, help="Antigravity model id")
    parser.add_argument("--model-label", help="Match a model by label from `ag models`")
    parser.add_argument("--launch-runtime", action="store_true", help="Launch Antigravity if model discovery requires it")


def add_send_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--idle-seconds", type=float, default=1.5)
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--attachment", action="append", default=[], type=Path)
    add_model_options(parser)


def add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def append_common_options(cmd: list[str], args: argparse.Namespace) -> list[str]:
    if getattr(args, "timeout", None) is not None:
        cmd += ["--timeout", str(args.timeout)]
    if getattr(args, "idle_seconds", None) is not None:
        cmd += ["--idle-seconds", str(args.idle_seconds)]
    if getattr(args, "poll", None) is not None:
        cmd += ["--poll", str(args.poll)]
    for path in getattr(args, "attachment", []) or []:
        cmd += ["--attachment", str(path)]
    return cmd


def humanize_models(models: Iterable[dict[str, Any]]) -> str:
    rows = []
    for item in models:
        default = "*" if item.get("default") else " "
        image = "images" if item.get("supportsImages") else ""
        tag = item.get("tagTitle") or ""
        suffix = " ".join(part for part in (image, tag) if part)
        rows.append(f"{default} {item['id']}\t{item['label']}" + (f"\t{suffix}" if suffix else ""))
    return "\n".join(rows)


def humanize_sessions(sessions: list[dict[str, Any]]) -> str:
    rows = []
    for item in sessions:
        title = item.get("title") or item.get("id") or ""
        updated = item.get("updated_local") or item.get("updated_at") or ""
        file_count = item.get("file_count", 0)
        rows.append(f"{item.get('id')}\t{updated}\tfiles={file_count}\t{title}")
    return "\n".join(rows)


def humanize_files(files: list[dict[str, Any]]) -> str:
    rows = []
    for item in files:
        rows.append(f"{item.get('name')}\t{item.get('kind')}\t{item.get('size')} bytes\t{item.get('summary')}")
    return "\n".join(rows)


def humanize_messages(messages: list[dict[str, Any]]) -> str:
    blocks = []
    for item in messages:
        role = item.get("role") or "message"
        content = item.get("content") or item.get("thought") or ""
        blocks.append(f"{role}: {content}".rstrip())
    return "\n\n".join(blocks)


def run_store_json(args: list[str]) -> Any:
    proc = run_capture(store_cmd() + args)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "store command failed").strip())
    text = proc.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def run_store_passthrough(args: list[str]) -> int:
    return run_passthrough(store_cmd() + args)


def check_path_writable(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".ag_doctor_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True, "path": str(path)}
    except Exception as exc:
        return {"ok": False, "path": str(path), "error": str(exc)}


def run_doctor(args: argparse.Namespace) -> int:
    from runtime_cli.ag_runtime import RuntimeLocator, RuntimeRpcClient

    checks: dict[str, Any] = {}

    checks["python"] = {
        "ok": sys.version_info >= (3, 11),
        "executable": sys.executable,
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        "hint": "Use Python 3.11+ for the installed CLI.",
    }
    checks["typer"] = {
        "ok": subprocess.run([python_with_module("typer"), "-c", "import typer"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0,
        "hint": "Run `./setup-ag.sh` if Typer is missing.",
    }

    locator = RuntimeLocator(REPO_ROOT)
    rows = locator._process_rows()
    checks["antigravity_process"] = {
        "ok": bool(rows),
        "count": len(rows),
        "hint": "Start Antigravity if no language_server process is running.",
    }

    connection = None
    if rows:
        try:
            connection = locator.discover()
            checks["language_server"] = {
                "ok": True,
                "pid": connection.pid,
                "port": connection.port,
                "use_tls": connection.use_tls,
                "has_csrf_token": bool(connection.csrf_token),
            }
        except Exception as exc:
            checks["language_server"] = {"ok": False, "error": str(exc)}
    else:
        checks["language_server"] = {"ok": False, "error": "no live Antigravity language_server process"}

    if connection:
        try:
            status = RuntimeRpcClient(connection).call("GetUserStatus", {})
            checks["get_user_status"] = {"ok": isinstance(status, dict), "keys": sorted(status.keys()) if isinstance(status, dict) else []}
        except Exception as exc:
            checks["get_user_status"] = {"ok": False, "error": str(exc)}
    else:
        checks["get_user_status"] = {"ok": False, "error": "language server unavailable"}

    try:
        models = load_models(launch_runtime=False)
        checks["models"] = {"ok": bool(models), "count": len(models), "default": next((m for m in models if m.get("default")), models[0] if models else None)}
    except Exception as exc:
        checks["models"] = {"ok": False, "error": str(exc)}

    cache_root = REPO_ROOT / ".cache"
    checks["cache_writable"] = check_path_writable(cache_root)
    checks["upload_writable"] = check_path_writable(cache_root / "_pending_uploads")

    ok = all(bool(item.get("ok")) for item in checks.values())
    payload = {"ok": ok, "checks": checks}
    if args.json:
        print_json(payload)
    else:
        for name, item in checks.items():
            marker = "ok" if item.get("ok") else "fail"
            print(f"{marker:4} {name}")
            if not item.get("ok"):
                detail = item.get("error") or item.get("hint")
                if detail:
                    print(f"     {detail}")
        if not ok:
            print("\nRun with --json for full diagnostic details.")
    return 0 if ok else 1


def cmd_models(args: argparse.Namespace) -> int:
    try:
        models = load_models(launch_runtime=args.launch_runtime)
        if args.json:
            print_json({"models": models})
        else:
            print(humanize_models(models))
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_ask(args: argparse.Namespace) -> int:
    return cmd_chat_start(args)


def cmd_chat_send(args: argparse.Namespace) -> int:
    try:
        cmd = ["send", args.message]
        if args.session:
            cmd += ["--session", args.session]
        model_id = resolve_model_id(args)
        if model_id is not None:
            cmd += ["--model", str(model_id)]
        append_common_options(cmd, args)
        if args.json:
            return run_store_passthrough(cmd)
        result = run_store_json(cmd)
        if isinstance(result, dict):
            if result.get("session_id"):
                sys.stderr.write(f"session_id: {result['session_id']}\n")
            answer = result.get("answer")
            if answer:
                print(answer)
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_chat_resume(args: argparse.Namespace) -> int:
    return cmd_chat_send(args)


def cmd_chat_start(args: argparse.Namespace) -> int:
    try:
        cmd = ["start" if args.async_start else "send", args.message]
        model_id = resolve_model_id(args)
        if model_id is not None:
            cmd += ["--model", str(model_id)]
        append_common_options(cmd, args)
        if args.json:
            return run_store_passthrough(cmd)
        result = run_store_json(cmd)
        if isinstance(result, dict):
            if result.get("session_id"):
                sys.stderr.write(f"session_id: {result['session_id']}\n")
            answer = result.get("answer")
            if answer:
                print(answer)
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_chat_stream(args: argparse.Namespace) -> int:
    try:
        cmd = ["stream", args.message]
        if args.session:
            cmd += ["--session", args.session]
        model_id = resolve_model_id(args)
        if model_id is not None:
            cmd += ["--model", str(model_id)]
        append_common_options(cmd, args)
        if args.json:
            return run_store_passthrough(cmd)

        proc = subprocess.Popen(store_cmd() + cmd, stdout=subprocess.PIPE, stderr=None, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "session":
                sys.stderr.write(f"session_id: {event.get('session_id')}\n")
                sys.stderr.flush()
            elif event_type == "delta":
                sys.stdout.write(str(event.get("delta") or ""))
                sys.stdout.flush()
            elif event_type == "done":
                if not str(event.get("answer") or "").endswith("\n"):
                    sys.stdout.write("\n")
                sys.stdout.flush()
        return int(proc.wait())
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_sessions_list(args: argparse.Namespace) -> int:
    try:
        result = run_store_json(["sessions", "list"])
        if args.json:
            print_json(result)
        else:
            print(humanize_sessions(result or []))
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_sessions_show(args: argparse.Namespace) -> int:
    try:
        result = run_store_json(["show", args.session_id] + (["--refresh"] if args.refresh else []))
        print_json(result)
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_sessions_files(args: argparse.Namespace) -> int:
    try:
        result = run_store_json(["sessions", "files", args.session_id])
        if args.json:
            print_json(result)
        else:
            print(humanize_files(result or []))
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_sessions_messages(args: argparse.Namespace) -> int:
    try:
        cmd = ["sessions", "messages", args.session_id]
        if args.refresh:
            cmd.append("--refresh")
        result = run_store_json(cmd)
        if args.json:
            print_json(result)
        else:
            print(humanize_messages(result or []))
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_attachments_bytes(args: argparse.Namespace) -> int:
    try:
        result = run_store_json(["attachment", "bytes", args.path])
        print_json(result)
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=True)


def cmd_cache_warm(args: argparse.Namespace) -> int:
    try:
        result = run_store_json(["cache", "warm", "--limit", str(args.limit)])
        print_json(result)
        return 0
    except Exception as exc:
        return fail(str(exc), json_output=args.json)


def cmd_ui_serve(args: argparse.Namespace) -> int:
    cmd = ["--host", args.host, "--port", str(args.port)]
    if args.open:
        cmd.append("--open")
    return run_passthrough(session_ui_cmd() + cmd)


def cmd_runtime_passthrough(args: argparse.Namespace) -> int:
    cmd = [args.runtime_cmd]
    if args.runtime_cmd == "send":
        cmd.append(args.message)
    elif args.runtime_cmd == "resume":
        cmd += ["--session", args.session, args.message]
    if args.runtime_cmd in {"send", "resume"}:
        model_id = resolve_model_id(args)
        if model_id is not None:
            cmd += ["--model", str(model_id)]
        cmd += ["--timeout", str(args.timeout), "--idle-seconds", str(args.idle_seconds), "--poll", str(args.poll)]
    elif args.runtime_cmd == "models":
        if args.fallback:
            cmd.append("--fallback")
        if args.launch_runtime:
            cmd.append("--launch-runtime")
    return run_passthrough(runtime_cmd() + cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ag", description="Antigravity sessions, chat, and local UI")
    parser.set_defaults(func=lambda args: parser.print_help() or 0)
    sub = parser.add_subparsers(dest="cmd")

    ask = sub.add_parser("ask", help="Create a new session and send one message")
    ask.add_argument("message")
    ask.add_argument("--async", dest="async_start", action="store_true", help="Return after creating the session and queueing send")
    add_send_options(ask)
    add_json_option(ask)
    ask.set_defaults(func=cmd_ask, session=None)

    models = sub.add_parser("models", help="List Antigravity UI model options")
    models.add_argument("--launch-runtime", action="store_true", help="Launch Antigravity if no language server is running")
    add_json_option(models)
    models.set_defaults(func=cmd_models)

    doctor = sub.add_parser("doctor", help="Diagnose Antigravity CLI/runtime setup")
    add_json_option(doctor)
    doctor.set_defaults(func=run_doctor)

    chat = sub.add_parser("chat", help="Chat operations")
    chat_sub = chat.add_subparsers(dest="chat_cmd", required=True)
    chat_send = chat_sub.add_parser("send", help="Send a message, creating a session when omitted")
    chat_send.add_argument("message")
    chat_send.add_argument("--session", help="Existing session id")
    add_send_options(chat_send)
    add_json_option(chat_send)
    chat_send.set_defaults(func=cmd_chat_send)

    chat_resume = chat_sub.add_parser("resume", help="Send to an existing session")
    chat_resume.add_argument("--session", "--chat", dest="session", required=True)
    chat_resume.add_argument("message")
    add_send_options(chat_resume)
    add_json_option(chat_resume)
    chat_resume.set_defaults(func=cmd_chat_resume)

    chat_start = chat_sub.add_parser("start", help="Create a new session and send")
    chat_start.add_argument("message")
    chat_start.add_argument("--async", dest="async_start", action="store_true", help="Return as soon as the session is created")
    add_send_options(chat_start)
    add_json_option(chat_start)
    chat_start.set_defaults(func=cmd_chat_start, session=None)

    chat_stream = chat_sub.add_parser("stream", help="Stream chat events or answer text")
    chat_stream.add_argument("message")
    chat_stream.add_argument("--session", help="Existing session id")
    add_send_options(chat_stream)
    add_json_option(chat_stream)
    chat_stream.set_defaults(func=cmd_chat_stream)

    sessions = sub.add_parser("sessions", help="Session read operations")
    sessions_sub = sessions.add_subparsers(dest="sessions_cmd", required=True)
    sessions_list = sessions_sub.add_parser("list", help="List sessions")
    add_json_option(sessions_list)
    sessions_list.set_defaults(func=cmd_sessions_list)
    sessions_show = sessions_sub.add_parser("show", help="Show one session")
    sessions_show.add_argument("session_id")
    sessions_show.add_argument("--refresh", action="store_true")
    add_json_option(sessions_show)
    sessions_show.set_defaults(func=cmd_sessions_show)
    sessions_files = sessions_sub.add_parser("files", help="List session files")
    sessions_files.add_argument("session_id")
    add_json_option(sessions_files)
    sessions_files.set_defaults(func=cmd_sessions_files)
    sessions_messages = sessions_sub.add_parser("messages", help="Show session messages")
    sessions_messages.add_argument("session_id")
    sessions_messages.add_argument("--refresh", action="store_true")
    add_json_option(sessions_messages)
    sessions_messages.set_defaults(func=cmd_sessions_messages)

    ui = sub.add_parser("ui", help="Local web UI")
    ui_sub = ui.add_subparsers(dest="ui_cmd", required=True)
    ui_serve = ui_sub.add_parser("serve", help="Run the local session web UI")
    ui_serve.add_argument("--host", default="127.0.0.1")
    ui_serve.add_argument("--port", type=int, default=8765)
    ui_serve.add_argument("--open", action="store_true")
    ui_serve.set_defaults(func=cmd_ui_serve)

    attachments = sub.add_parser("attachments", help="Attachment read operations")
    attachments_sub = attachments.add_subparsers(dest="attachments_cmd", required=True)
    attachment_bytes = attachments_sub.add_parser("bytes", help="Read attachment bytes as base64 JSON")
    attachment_bytes.add_argument("path")
    attachment_bytes.set_defaults(func=cmd_attachments_bytes)

    cache = sub.add_parser("cache", help="Cache operations")
    cache_sub = cache.add_subparsers(dest="cache_cmd", required=True)
    cache_warm = cache_sub.add_parser("warm", help="Warm session message cache")
    cache_warm.add_argument("--limit", type=int, default=0)
    add_json_option(cache_warm)
    cache_warm.set_defaults(func=cmd_cache_warm)

    runtime = sub.add_parser("runtime", help="Headless language-server operations")
    runtime_sub = runtime.add_subparsers(dest="runtime_cmd", required=True)
    runtime_send = runtime_sub.add_parser("send", help="Start a runtime session and print answer")
    runtime_send.add_argument("message")
    runtime_send.add_argument("--timeout", type=float, default=60.0)
    runtime_send.add_argument("--idle-seconds", type=float, default=1.5)
    runtime_send.add_argument("--poll", type=float, default=0.5)
    add_model_options(runtime_send)
    runtime_send.set_defaults(func=cmd_runtime_passthrough)
    runtime_resume = runtime_sub.add_parser("resume", help="Resume a runtime session")
    runtime_resume.add_argument("--session", required=True)
    runtime_resume.add_argument("message")
    runtime_resume.add_argument("--timeout", type=float, default=60.0)
    runtime_resume.add_argument("--idle-seconds", type=float, default=1.5)
    runtime_resume.add_argument("--poll", type=float, default=0.5)
    add_model_options(runtime_resume)
    runtime_resume.set_defaults(func=cmd_runtime_passthrough)
    runtime_models = runtime_sub.add_parser("models", help="List runtime model options")
    runtime_models.add_argument("--fallback", action="store_true")
    runtime_models.add_argument("--launch-runtime", action="store_true")
    runtime_models.set_defaults(func=cmd_runtime_passthrough)

    send = sub.add_parser("send", help="Alias for `chat resume`")
    send.add_argument("--chat", "--session", dest="session", required=True)
    send.add_argument("message")
    send.add_argument("--timeout", type=float, default=60.0)
    send.add_argument("--idle-seconds", type=float, default=1.5)
    send.add_argument("--poll", type=float, default=0.5)
    send.add_argument("--attachment", action="append", default=[], type=Path)
    add_model_options(send)
    add_json_option(send)
    send.set_defaults(func=cmd_chat_resume)

    resume = sub.add_parser("resume", help="Alias for `chat resume`")
    resume.add_argument("--chat", "--session", dest="session", required=True)
    resume.add_argument("message")
    resume.add_argument("--timeout", type=float, default=60.0)
    resume.add_argument("--idle-seconds", type=float, default=1.5)
    resume.add_argument("--poll", type=float, default=0.5)
    resume.add_argument("--attachment", action="append", default=[], type=Path)
    add_model_options(resume)
    add_json_option(resume)
    resume.set_defaults(func=cmd_chat_resume)

    webui = sub.add_parser("webui", help="Alias for `ui serve`")
    webui.add_argument("--host", default="127.0.0.1")
    webui.add_argument("--port", type=int, default=8765)
    webui.add_argument("--open", action="store_true")
    webui.set_defaults(func=cmd_ui_serve)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return fail("interrupted", json_output=bool(getattr(args, "json", False)), code=130)
    except Exception as exc:
        return fail(str(exc), json_output=bool(getattr(args, "json", False)))


if __name__ == "__main__":
    raise SystemExit(main())
