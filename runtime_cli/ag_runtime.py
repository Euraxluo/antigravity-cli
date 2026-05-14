#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, TextIO, Tuple


try:
    from .model_registry import DEFAULT_MODEL_ID, load_model_options
except ImportError:
    from model_registry import DEFAULT_MODEL_ID, load_model_options


@dataclass(frozen=True)
class RuntimeConnection:
    pid: int
    port: int
    use_tls: bool
    csrf_token: str
    extension_server_port: int


class RuntimeLocator:
    def __init__(self, cwd: Optional[Path] = None) -> None:
        env_cwd = os.environ.get("AG_WORKSPACE", "").strip()
        base = Path(env_cwd).expanduser() if env_cwd else (cwd or Path.cwd())
        self.cwd = base.resolve()

    def discover(self) -> RuntimeConnection:
        rows = self._process_rows()
        if not rows:
            self._launch_runtime()
            rows = self._wait_for_process_rows()
        if not rows:
            raise RuntimeError(
                "No live Antigravity language_server process found after launching `antigravity -n`."
            )

        errors: List[str] = []
        for pid, args in self._ordered_process_candidates(rows, self._workspace_hints()):
            csrf_token = self._extract_arg(args, "csrf_token")
            if not csrf_token:
                errors.append(f"pid {pid}: missing csrf token")
                continue

            ext_port_raw = self._extract_arg(args, "extension_server_port")
            extension_server_port = int(ext_port_raw) if ext_port_raw and ext_port_raw.isdigit() else 0

            ports = self._listening_ports(pid)
            if not ports:
                errors.append(f"pid {pid}: no listening TCP ports")
                continue

            try:
                port, use_tls = self._find_connect_port(ports, extension_server_port)
            except Exception as exc:
                errors.append(f"pid {pid}: {exc}")
                continue

            return RuntimeConnection(
                pid=pid,
                port=port,
                use_tls=use_tls,
                csrf_token=csrf_token,
                extension_server_port=extension_server_port,
            )

        raise RuntimeError(
            "Failed to discover a working language_server process. "
            + "; ".join(errors[:8])
        )

    def _wait_for_process_rows(self, timeout: float = 40.0, poll: float = 0.5) -> List[Tuple[int, str]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self._process_rows()
            if rows:
                return rows
            time.sleep(poll)
        return []

    def _workspace_hints(self) -> List[str]:
        env_hint = os.environ.get("AG_RUNTIME_WORKSPACE_HINT", "").strip().lower()
        hints = []
        if env_hint:
            hints.append(env_hint)

        parts = [part for part in self.cwd.parts if part and part != os.sep]
        if parts:
            hints.append(parts[-1].replace("-", "_").replace(".", "_").lower())
        if len(parts) >= 2:
            hints.append("_".join(parts[-2:]).replace("-", "_").replace(".", "_").lower())
        return list(dict.fromkeys(hints))

    def _process_rows(self) -> List[Tuple[int, str]]:
        proc = subprocess.run(
            ["ps", "-eo", "pid,args"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return self.parse_process_rows(proc.stdout)

    def _launch_runtime(self) -> None:
        try:
            subprocess.Popen(
                self.build_launch_command(self.cwd),
                cwd=str(self.cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Failed to launch runtime because `antigravity` was not found in PATH."
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to launch runtime with `antigravity -n`: {exc}") from exc

    @staticmethod
    def parse_process_rows(output: str) -> List[Tuple[int, str]]:
        rows: List[Tuple[int, str]] = []
        for line in output.splitlines():
            if "language_server" not in line or "csrf_token" not in line:
                continue
            match = re.match(r"\s*(\d+)\s+(.*)", line)
            if not match:
                continue
            rows.append((int(match.group(1)), match.group(2)))
        return rows

    @staticmethod
    def build_launch_command(cwd: Path) -> List[str]:
        del cwd
        return ["antigravity", "-n"]

    @staticmethod
    def _select_process(rows: Sequence[Tuple[int, str]], hints: Sequence[str]) -> Tuple[int, str]:
        if not rows:
            raise RuntimeError("No candidate language_server processes")
        lowered_hints = [hint.lower() for hint in hints if hint]
        for hint in lowered_hints:
            for pid, args in rows:
                if hint in args.lower():
                    return pid, args
        return rows[-1]

    @staticmethod
    def _ordered_process_candidates(rows: Sequence[Tuple[int, str]], hints: Sequence[str]) -> List[Tuple[int, str]]:
        if not rows:
            return []
        lowered_hints = [hint.lower() for hint in hints if hint]
        preferred: List[Tuple[int, str]] = []
        fallback: List[Tuple[int, str]] = []
        for row in rows:
            pid, args = row
            text = args.lower()
            if any(hint in text for hint in lowered_hints):
                preferred.append(row)
            else:
                fallback.append(row)
        return preferred + fallback

    @staticmethod
    def _extract_arg(cmdline: str, name: str) -> Optional[str]:
        eq_match = re.search(rf"--{re.escape(name)}=([^\s\"]+)", cmdline)
        if eq_match:
            return eq_match.group(1)
        space_match = re.search(rf"--{re.escape(name)}\s+([^\s\"]+)", cmdline)
        if space_match:
            return space_match.group(1)
        return None

    def _listening_ports(self, pid: int) -> List[int]:
        proc = subprocess.run(
            ["lsof", "-Pan", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        ports: List[int] = []
        for line in proc.stdout.splitlines():
            match = re.search(r":(\d+)\s+\(LISTEN\)", line)
            if not match:
                continue
            port = int(match.group(1))
            if port not in ports:
                ports.append(port)
        return ports

    def _find_connect_port(self, ports: Sequence[int], extension_server_port: int) -> Tuple[int, bool]:
        candidates = [port for port in ports if port != extension_server_port] or list(ports)
        for port in candidates:
            if self._probe_port(port, use_tls=True):
                return port, True
        for port in candidates:
            if self._probe_port(port, use_tls=False):
                return port, False
        raise RuntimeError(
            f"Failed to discover a working ConnectRPC port from listening ports: {', '.join(str(p) for p in ports)}"
        )

    def _probe_port(self, port: int, use_tls: bool) -> bool:
        url = self._endpoint_url(port, use_tls, "GetUserStatus")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": "2",
        }
        try:
            response = self._urlopen(url, b"{}", headers=headers, use_tls=use_tls, timeout=2.0)
            try:
                response.read()
            finally:
                response.close()
            return True
        except urllib.error.HTTPError as exc:
            return exc.code in {200, 401, 403, 415}
        except Exception:
            return False

    @staticmethod
    def _endpoint_url(port: int, use_tls: bool, method: str) -> str:
        scheme = "https" if use_tls else "http"
        return f"{scheme}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/{method}"

    @staticmethod
    def _urlopen(
        url: str,
        body: bytes,
        *,
        headers: dict[str, str],
        use_tls: bool,
        timeout: float,
    ):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return urllib.request.urlopen(request, timeout=timeout, context=ctx)
        return urllib.request.urlopen(request, timeout=timeout)


class RuntimeRpcClient:
    def __init__(self, connection: RuntimeConnection) -> None:
        self.connection = connection

    def call(self, method: str, payload: dict[str, Any]) -> Any:
        url = RuntimeLocator._endpoint_url(self.connection.port, self.connection.use_tls, method)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Connect-Protocol-Version": "1",
            "X-Codeium-Csrf-Token": self.connection.csrf_token,
        }
        try:
            response = RuntimeLocator._urlopen(
                url,
                body,
                headers=headers,
                use_tls=self.connection.use_tls,
                timeout=10.0,
            )
            try:
                raw = response.read().decode("utf-8", errors="replace")
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Runtime RPC {method} failed: HTTP {exc.code}: {detail[:240]}") from exc
        except Exception as exc:
            raise RuntimeError(f"Runtime RPC {method} failed: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def start_cascade(self) -> str:
        result = self.call("StartCascade", {"source": 0})
        cascade_id = result.get("cascadeId") if isinstance(result, dict) else None
        if not cascade_id:
            raise RuntimeError(f"StartCascade returned no cascadeId: {result!r}")
        return str(cascade_id)

    def send_user_message(
        self,
        cascade_id: str,
        text: str,
        *,
        model: int = DEFAULT_MODEL_ID,
        attachment_paths: Optional[Sequence[Path]] = None,
    ) -> Any:
        payload = self.build_send_payload(cascade_id, text, model=model, attachment_paths=attachment_paths)
        return self.call("SendUserCascadeMessage", payload)

    def get_trajectory_steps(self, cascade_id: str, *, end_index: int = 2000) -> List[dict[str, Any]]:
        result = self.call(
            "GetCascadeTrajectorySteps",
            {"cascadeId": cascade_id, "startIndex": 0, "endIndex": end_index},
        )
        if isinstance(result, dict) and isinstance(result.get("steps"), list):
            return list(result["steps"])
        return []

    @staticmethod
    def build_send_payload(
        cascade_id: str,
        text: str,
        *,
        model: int = DEFAULT_MODEL_ID,
        attachment_paths: Optional[Sequence[Path]] = None,
    ) -> dict[str, Any]:
        return {
            "cascadeId": cascade_id,
            "items": RuntimeRpcClient.build_items(text, attachment_paths or []),
            "cascadeConfig": {
                "plannerConfig": {
                    "requestedModel": {"model": model},
                },
            },
        }

    @staticmethod
    def build_items(text: str, attachment_paths: Sequence[Path]) -> List[dict[str, Any]]:
        items: List[dict[str, Any]] = []
        for path in attachment_paths:
            items.append({"item": {"file": {"absoluteUri": path.resolve().as_uri()}}})
        if text:
            items.append({"text": text})
        return items


class AnswerCollector:
    def __init__(
        self,
        client: RuntimeRpcClient,
        *,
        poll_interval: float,
        idle_seconds: float,
        timeout: float,
        stdout: TextIO,
    ) -> None:
        self.client = client
        self.poll_interval = poll_interval
        self.idle_seconds = idle_seconds
        self.timeout = timeout
        self.stdout = stdout

    def capture_baseline(self, cascade_id: str) -> int:
        return len(self.client.get_trajectory_steps(cascade_id))

    def collect(self, cascade_id: str, *, baseline_step_count: int) -> str:
        last_text = ""
        for event in self.iter_events(cascade_id, baseline_step_count=baseline_step_count):
            if event["type"] == "delta":
                last_text = self.emit_delta(last_text, event["full"], self.stdout)
        if last_text and not last_text.endswith("\n"):
            self.stdout.write("\n")
            self.stdout.flush()
        return last_text

    def iter_events(self, cascade_id: str, *, baseline_step_count: int):
        last_text = ""
        started_at = time.monotonic()
        last_change_at = started_at
        saw_output = False

        while True:
            now = time.monotonic()
            if now - started_at > self.timeout:
                break

            steps = self.client.get_trajectory_steps(cascade_id)
            current_text = self.extract_assistant_text(steps, baseline_step_count)
            if current_text != last_text:
                delta = self.compute_delta(last_text, current_text)
                last_text = current_text
                last_change_at = now
                saw_output = bool(last_text)
                if delta:
                    yield {"type": "delta", "delta": delta, "full": current_text}

            if saw_output and now - last_change_at >= self.idle_seconds:
                break

            time.sleep(self.poll_interval)

        yield {"type": "done", "full": last_text}

    @staticmethod
    def extract_assistant_text(steps: Sequence[dict[str, Any]], baseline_step_count: int) -> str:
        parts: List[str] = []
        for step in steps[baseline_step_count:]:
            step_type = step.get("type")
            if step_type == "CORTEX_STEP_TYPE_NOTIFY_USER":
                notify = step.get("notifyUser") or {}
                content = notify.get("notificationContent")
                if isinstance(content, str) and content:
                    parts.append(content)
                continue

            if step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
                planner = step.get("plannerResponse") or {}
                content = planner.get("modifiedResponse") or planner.get("response")
                if isinstance(content, str) and content:
                    parts.append(content)
                continue

            if step_type == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
                error_message = step.get("errorMessage") or {}
                error = error_message.get("error") or {}
                content = error.get("userErrorMessage") or error.get("shortError")
                if isinstance(content, str) and content:
                    parts.append(content)
        return "\n\n".join(parts)

    @staticmethod
    def emit_delta(previous: str, current: str, stream: TextIO) -> str:
        delta = AnswerCollector.compute_delta(previous, current)
        if delta:
            stream.write(delta)
            stream.flush()
        return current

    @staticmethod
    def compute_delta(previous: str, current: str) -> str:
        if not current:
            return ""
        if not previous:
            return current
        if current.startswith(previous):
            return current[len(previous):]
        common = 0
        for left, right in zip(previous, current):
            if left != right:
                break
            common += 1
        return "\n" + current


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ag-runtime", description="Minimal headless Antigravity runtime CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    send = sub.add_parser("send", help="Start a new runtime session and print the answer")
    send.add_argument("message", help="Prompt text")
    send.add_argument("--timeout", type=float, default=60.0, help="Overall wait timeout in seconds")
    send.add_argument("--idle-seconds", type=float, default=1.5, help="Stop after this long without answer changes")
    send.add_argument("--poll", type=float, default=0.5, help="Polling interval in seconds")
    send.add_argument("--model", type=int, default=DEFAULT_MODEL_ID, help="Requested model id")
    send.set_defaults(func=_run_send)

    resume = sub.add_parser("resume", help="Send to an existing runtime session and print the answer")
    resume.add_argument("--session", required=True, help="Existing cascade id")
    resume.add_argument("message", help="Prompt text")
    resume.add_argument("--timeout", type=float, default=60.0, help="Overall wait timeout in seconds")
    resume.add_argument("--idle-seconds", type=float, default=1.5, help="Stop after this long without answer changes")
    resume.add_argument("--poll", type=float, default=0.5, help="Polling interval in seconds")
    resume.add_argument("--model", type=int, default=DEFAULT_MODEL_ID, help="Requested model id")
    resume.set_defaults(func=_run_resume)

    models = sub.add_parser("models", help="List Antigravity UI model options")
    models.add_argument("--fallback", action="store_true", help="Skip live Antigravity lookup and print fallback/config options")
    models.add_argument("--launch-runtime", action="store_true", help="Launch Antigravity if no language server is running")
    models.set_defaults(func=_run_models)
    return parser


def _run_send(args: argparse.Namespace) -> int:
    locator = RuntimeLocator()
    connection = locator.discover()
    client = RuntimeRpcClient(connection)
    collector = AnswerCollector(
        client,
        poll_interval=args.poll,
        idle_seconds=args.idle_seconds,
        timeout=args.timeout,
        stdout=sys.stdout,
    )
    cascade_id = client.start_cascade()
    sys.stderr.write(f"session_id: {cascade_id}\n")
    sys.stderr.flush()
    baseline = collector.capture_baseline(cascade_id)
    client.send_user_message(cascade_id, args.message, model=args.model)
    text = collector.collect(cascade_id, baseline_step_count=baseline)
    if not text:
        raise RuntimeError("No assistant output observed before timeout")
    return 0


def _run_resume(args: argparse.Namespace) -> int:
    locator = RuntimeLocator()
    connection = locator.discover()
    client = RuntimeRpcClient(connection)
    collector = AnswerCollector(
        client,
        poll_interval=args.poll,
        idle_seconds=args.idle_seconds,
        timeout=args.timeout,
        stdout=sys.stdout,
    )
    baseline = collector.capture_baseline(args.session)
    client.send_user_message(args.session, args.message, model=args.model)
    text = collector.collect(args.session, baseline_step_count=baseline)
    if not text:
        raise RuntimeError("No assistant output observed before timeout")
    return 0


def _run_models(args: argparse.Namespace) -> int:
    options = load_model_options(dynamic=not args.fallback, launch=args.launch_runtime)
    print(json.dumps(options, ensure_ascii=False))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        sys.stderr.flush()
        return 130
    except RuntimeError as exc:
        sys.stderr.write(f"error: {exc}\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
