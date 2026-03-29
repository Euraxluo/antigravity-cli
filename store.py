#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
import io
import mimetypes
import json
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Sequence
import threading


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime_cli.ag_runtime import AnswerCollector, RuntimeLocator, RuntimeRpcClient


@dataclass
class SessionSummary:
    id: str
    title: str
    updated_at: str
    updated_local: str
    file_count: int
    has_messages: bool
    workspace_path: Optional[str] = None


@dataclass
class SessionFile:
    name: str
    updated_at: str
    size: int
    summary: str
    kind: str
    mime_type: str


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: str
    step_index: Optional[int]
    thought: Optional[str] = None
    attachments: Optional[List[Dict[str, str]]] = None


class AntigravitySessionStore:
    def __init__(
        self,
        conversations_dir: Optional[Path] = None,
        brain_dir: Optional[Path] = None,
    ) -> None:
        home = Path.home()
        self.repo_root = Path(__file__).resolve().parent
        self.cache_root = self.repo_root / ".cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.conversations_dir = conversations_dir or home / ".gemini" / "antigravity" / "conversations"
        self.brain_dir = brain_dir or home / ".gemini" / "antigravity" / "brain"

    def get_session_summary(self, session_id: str, fallback_title: Optional[str] = None) -> Dict[str, Any]:
        pb_path = self.conversations_dir / f"{session_id}.pb"
        if pb_path.exists():
            updated = dt.datetime.fromtimestamp(pb_path.stat().st_mtime, tz=dt.timezone.utc)
        else:
            updated = dt.datetime.now(dt.timezone.utc)

        title = self._session_title(session_id)
        if title == session_id and fallback_title:
            title = fallback_title.strip()[:80] or session_id

        return asdict(
            SessionSummary(
                id=session_id,
                title=title,
                updated_at=updated.isoformat(),
                updated_local=updated.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                file_count=len(self.list_session_files(session_id)),
                has_messages=self._messages_cache_path(session_id).exists(),
                workspace_path=self.session_workspace_path(session_id),
            )
        )

    def send_message(
        self,
        message: str,
        *,
        session_id: Optional[str] = None,
        timeout: float = 60.0,
        idle_seconds: float = 1.5,
        poll: float = 0.5,
        model: int = 1018,
        attachment_paths: Optional[Sequence[Path]] = None,
    ) -> Dict[str, Any]:
        prompt = (message or "").strip()
        paths = list(attachment_paths or [])
        if not prompt and not paths:
            raise ValueError("message or attachment is required")

        locator = RuntimeLocator(self.repo_root)
        connection = locator.discover()
        client = RuntimeRpcClient(connection)
        stream = io.StringIO()
        collector = AnswerCollector(
            client,
            poll_interval=poll,
            idle_seconds=idle_seconds,
            timeout=timeout,
            stdout=stream,
        )

        created = session_id is None
        if created:
            session_id = client.start_cascade()

        assert session_id is not None
        paths = [self._move_upload_into_session(session_id, path) for path in paths]
        baseline = collector.capture_baseline(session_id)
        client.send_user_message(session_id, prompt, model=model, attachment_paths=paths)
        answer = collector.collect(session_id, baseline_step_count=baseline).strip()
        messages = self.get_session_messages(session_id, force_refresh=True)

        return {
            "session_id": session_id,
            "created": created,
            "answer": answer,
            "messages": messages,
            "session": self.get_session_summary(session_id, fallback_title=prompt),
        }

    def start_session_send(
        self,
        message: str,
        *,
        timeout: float = 60.0,
        idle_seconds: float = 1.5,
        poll: float = 0.5,
        model: int = 1018,
        attachment_paths: Optional[Sequence[Path]] = None,
    ) -> Dict[str, Any]:
        prompt = (message or "").strip()
        paths = list(attachment_paths or [])
        if not prompt and not paths:
            raise ValueError("message or attachment is required")

        locator = RuntimeLocator(self.repo_root)
        connection = locator.discover()
        client = RuntimeRpcClient(connection)
        session_id = client.start_cascade()
        paths = [self._move_upload_into_session(session_id, path) for path in paths]

        cached_messages = [
            asdict(
                ChatMessage(
                    "user",
                    self._user_response_from_prompt_and_paths(prompt, paths),
                    dt.datetime.now(dt.timezone.utc).isoformat(),
                    None,
                    attachments=self._attachments_from_paths(paths) or None,
                )
            )
        ]
        self._write_json(self._messages_cache_path(session_id), cached_messages)

        threading.Thread(
            target=self._send_message_background,
            kwargs={
                "session_id": session_id,
                "message": prompt,
                "timeout": timeout,
                "idle_seconds": idle_seconds,
                "poll": poll,
                "model": model,
                "attachment_paths": paths,
            },
            daemon=True,
        ).start()

        return {
            "session_id": session_id,
            "created": True,
            "answer": "",
            "messages": cached_messages,
            "session": self.get_session_summary(session_id, fallback_title=prompt),
        }

    def stream_message(
        self,
        message: str,
        *,
        session_id: Optional[str] = None,
        timeout: float = 60.0,
        idle_seconds: float = 1.5,
        poll: float = 0.5,
        model: int = 1018,
        attachment_paths: Optional[Sequence[Path]] = None,
    ):
        prompt = (message or "").strip()
        paths = list(attachment_paths or [])
        if not prompt and not paths:
            raise ValueError("message or attachment is required")

        locator = RuntimeLocator(self.repo_root)
        connection = locator.discover()
        client = RuntimeRpcClient(connection)
        collector = AnswerCollector(
            client,
            poll_interval=poll,
            idle_seconds=idle_seconds,
            timeout=timeout,
            stdout=io.StringIO(),
        )

        created = session_id is None
        if created:
            session_id = client.start_cascade()

        assert session_id is not None
        paths = [self._move_upload_into_session(session_id, path) for path in paths]

        cached_messages = [
            asdict(
                ChatMessage(
                    "user",
                    self._user_response_from_prompt_and_paths(prompt, paths),
                    dt.datetime.now(dt.timezone.utc).isoformat(),
                    None,
                    attachments=self._attachments_from_paths(paths) or None,
                )
            )
        ]
        self._write_json(self._messages_cache_path(session_id), cached_messages)

        yield {
            "type": "session",
            "session_id": session_id,
            "created": created,
            "messages": cached_messages,
            "session": self.get_session_summary(session_id, fallback_title=prompt),
        }

        baseline = collector.capture_baseline(session_id)
        client.send_user_message(session_id, prompt, model=model, attachment_paths=paths)

        assistant_text = ""
        for event in collector.iter_events(session_id, baseline_step_count=baseline):
            if event["type"] == "delta":
                assistant_text = event["full"]
                yield {
                    "type": "delta",
                    "session_id": session_id,
                    "delta": event["delta"],
                    "full": assistant_text,
                }
                continue

            messages = self.get_session_messages(session_id, force_refresh=True)
            yield {
                "type": "done",
                "session_id": session_id,
                "answer": assistant_text,
                "messages": messages,
                "session": self.get_session_summary(session_id, fallback_title=prompt),
            }

    def save_uploaded_file(self, filename: str, fileobj: BinaryIO) -> Path:
        safe_name = Path(filename or "upload.bin").name or "upload.bin"
        upload_dir = self.cache_root / "_pending_uploads" / dt.datetime.now().strftime("%Y%m%d")
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{int(time.time() * 1000)}_{safe_name}"
        with target.open("wb") as f:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return target

    def _send_message_background(
        self,
        *,
        session_id: str,
        message: str,
        timeout: float,
        idle_seconds: float,
        poll: float,
        model: int,
        attachment_paths: Sequence[Path],
    ) -> None:
        try:
            self.send_message(
                message,
                session_id=session_id,
                timeout=timeout,
                idle_seconds=idle_seconds,
                poll=poll,
                model=model,
                attachment_paths=attachment_paths,
            )
        except Exception as exc:
            cached = []
            cache_path = self._messages_cache_path(session_id)
            if cache_path.exists():
                try:
                    cached = self._read_json(cache_path)
                except Exception:
                    cached = []
            cached.append(
                asdict(
                    ChatMessage(
                        "assistant",
                        f"error: {exc}",
                        dt.datetime.now(dt.timezone.utc).isoformat(),
                        None,
                    )
                )
            )
            self._write_json(cache_path, cached)

    def list_sessions(self) -> List[Dict[str, Any]]:
        session_ids: set[str] = set()
        sessions: List[SessionSummary] = []
        workspace_map = self._live_workspace_map()

        for pb in sorted(self.conversations_dir.glob("*.pb"), key=lambda p: p.stat().st_mtime, reverse=True):
            session_id = pb.stem
            session_ids.add(session_id)
            files = self.list_session_files(session_id)
            updated = dt.datetime.fromtimestamp(pb.stat().st_mtime, tz=dt.timezone.utc)
            sessions.append(
                SessionSummary(
                    id=session_id,
                    title=self._session_title(session_id),
                    updated_at=updated.isoformat(),
                    updated_local=updated.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    file_count=len(files),
                    has_messages=self._messages_cache_path(session_id).exists(),
                    workspace_path=workspace_map.get(session_id) or self.session_workspace_path(session_id),
                )
            )

        for cache_dir in sorted(self.cache_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not cache_dir.is_dir():
                continue
            session_id = cache_dir.name
            if session_id in session_ids:
                continue
            sessions.append(
                SessionSummary(
                    id=session_id,
                    title=self._session_title(session_id),
                    updated_at=dt.datetime.fromtimestamp(cache_dir.stat().st_mtime, tz=dt.timezone.utc).isoformat(),
                    updated_local=dt.datetime.fromtimestamp(cache_dir.stat().st_mtime, tz=dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    file_count=len(self.list_session_files(session_id)),
                    has_messages=self._messages_cache_path(session_id).exists(),
                    workspace_path=workspace_map.get(session_id) or self.session_workspace_path(session_id),
                )
            )

        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return [asdict(item) for item in sessions]

    def session_workspace_path(self, session_id: str) -> Optional[str]:
        workspace_map = self._live_workspace_map()
        if session_id in workspace_map:
            return workspace_map[session_id]

        # 1. Try to infer from transcript cache.
        messages = self.get_session_messages(session_id)
        for msg in messages:
            content = str(msg.get("content") or "")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("@[/") and "]" in stripped:
                    candidate = stripped[2:].split("]", 1)[0]
                    p = Path(candidate)
                    if p.exists():
                        return str(p.parent if p.is_file() else p)
                if stripped.startswith("/Users/echo/"):
                    p = Path(stripped)
                    if p.exists():
                        return str(p if p.is_dir() else p.parent)

        # 2. Try to infer from text artifacts.
        session_dir = self.brain_dir / session_id
        for name in ("task.md", "implementation_plan.md", "walkthrough.md"):
            path = session_dir / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in re.findall(r"/Users/echo/[^\s)`'\"<>]+", text):
                p = Path(match)
                if p.exists():
                    return str(p if p.is_dir() else p.parent)

        return None

    def _live_workspace_map(self) -> Dict[str, str]:
        summary_result = self._live_ls_rpc("GetAllCascadeTrajectories", {})
        summaries = summary_result.get("trajectorySummaries") if isinstance(summary_result, dict) else None
        if not isinstance(summaries, dict):
            return {}

        workspace_map: Dict[str, str] = {}
        for session_id, summary in summaries.items():
            if not isinstance(summary, dict):
                continue
            workspaces = summary.get("workspaces")
            if not isinstance(workspaces, list):
                continue
            for workspace in workspaces:
                if not isinstance(workspace, dict):
                    continue
                uri = workspace.get("workspaceFolderAbsoluteUri") or workspace.get("gitRootAbsoluteUri")
                if not isinstance(uri, str) or not uri:
                    continue
                parsed = urllib.parse.urlparse(uri)
                if parsed.scheme != "file":
                    continue
                path = urllib.parse.unquote(parsed.path)
                if path:
                    workspace_map[str(session_id)] = path
                    break
        return workspace_map

    def list_session_files(self, session_id: str) -> List[Dict[str, Any]]:
        files: List[SessionFile] = []
        candidate_dirs = [self.brain_dir / session_id, self._session_upload_dir(session_id)]
        for directory in candidate_dirs:
            if not directory.exists():
                continue
            for path in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
                if not path.is_file():
                    continue
                if self._is_hidden_or_internal(path.name):
                    continue
                stat = path.stat()
                files.append(
                    SessionFile(
                        name=path.name,
                        updated_at=dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat(),
                        size=stat.st_size,
                        summary=self._artifact_summary(path),
                        kind=self._file_kind(path),
                        mime_type=self._mime_type(path),
                    )
                )
        files.sort(key=lambda item: item.name.lower())
        return [asdict(item) for item in files]

    def get_session_file_content(self, session_id: str, file_name: str) -> Dict[str, Any]:
        path = self._resolve_session_file_path(session_id, file_name)
        return {
            "session_id": session_id,
            "name": file_name,
            "kind": self._file_kind(path),
            "mime_type": self._mime_type(path),
            "content": path.read_text(encoding="utf-8", errors="replace"),
        }

    def get_session_file_bytes(self, session_id: str, file_name: str) -> Dict[str, Any]:
        path = self._resolve_session_file_path(session_id, file_name)
        return {
            "session_id": session_id,
            "name": file_name,
            "mime_type": self._mime_type(path),
            "bytes": path.read_bytes(),
        }

    def _resolve_session_file_path(self, session_id: str, file_name: str) -> Path:
        allowed = {item["name"] for item in self.list_session_files(session_id)}
        if file_name not in allowed:
            raise FileNotFoundError(f"file not found for session {session_id}: {file_name}")
        for directory in (self.brain_dir / session_id, self._session_upload_dir(session_id)):
            path = directory / file_name
            if path.exists() and path.is_file():
                return path
        raise FileNotFoundError(f"file not found for session {session_id}: {file_name}")

    def _session_upload_dir(self, session_id: str) -> Path:
        path = self.cache_root / session_id / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _move_upload_into_session(self, session_id: str, path: Path) -> Path:
        source = Path(path)
        if not source.exists():
            return source
        target_dir = self._session_upload_dir(session_id)
        target = target_dir / source.name
        if source.resolve() == target.resolve():
            return target
        counter = 1
        while target.exists():
            target = target_dir / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        source.replace(target)
        return target

    def get_attachment_bytes(self, path_str: str) -> Dict[str, Any]:
        path = Path(path_str).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"attachment not found: {path}")
        return {
            "name": path.name,
            "mime_type": self._mime_type(path),
            "bytes": path.read_bytes(),
        }

    def get_session_messages(self, session_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        cache_path = self._messages_cache_path(session_id)
        if not force_refresh and cache_path.exists():
            return self._read_json(cache_path)

        if not force_refresh:
            legacy_steps = self._legacy_steps_cache_path(session_id)
            if legacy_steps.exists():
                try:
                    payload = self._read_json(legacy_steps)
                    messages = self._steps_to_messages(payload.get("steps") or [])
                    if messages:
                        self._write_json(cache_path, messages)
                        return messages
                except Exception:
                    pass

        live_steps = self._live_ls_rpc(
            "GetCascadeTrajectorySteps",
            {"cascadeId": session_id, "startIndex": 0, "endIndex": 2000},
        )
        if isinstance(live_steps, dict) and isinstance(live_steps.get("steps"), list):
            messages = self._steps_to_messages(live_steps["steps"])
            if messages:
                self._write_json(cache_path, messages)
                self._write_json(self._steps_cache_path(session_id), live_steps)
                return messages

        if cache_path.exists():
            return self._read_json(cache_path)
        return []

    def warm_all_messages(self, limit: Optional[int] = None) -> Dict[str, Any]:
        processed = 0
        succeeded = 0
        failed: List[Dict[str, str]] = []

        for pb in sorted(self.conversations_dir.glob("*.pb"), key=lambda p: p.stat().st_mtime, reverse=True):
            if limit is not None and processed >= limit:
                break
            processed += 1
            session_id = pb.stem
            messages = self.get_session_messages(session_id, force_refresh=True)
            if messages:
                succeeded += 1
            else:
                failed.append({"session_id": session_id, "error": "message fetch failed"})

        return {
            "processed": processed,
            "succeeded": succeeded,
            "failed_count": len(failed),
            "failed": failed[:20],
        }

    def _session_title(self, session_id: str) -> str:
        session_dir = self.brain_dir / session_id
        for name in ("task.md", "walkthrough.md", "implementation_plan.md"):
            path = session_dir / name
            if not path.exists():
                continue
            heading = self._first_heading(path.read_text(encoding="utf-8", errors="replace"))
            if heading:
                return heading
        cache_path = self._messages_cache_path(session_id)
        if cache_path.exists():
            try:
                messages = self._read_json(cache_path)
                for item in messages:
                    if item.get("role") == "user" and item.get("content"):
                        return str(item["content"]).strip()[:80]
            except Exception:
                pass
        return session_id

    def _artifact_summary(self, path: Path) -> str:
        if self._file_kind(path) == "image":
            return path.name
        text = path.read_text(encoding="utf-8", errors="replace")
        heading = self._first_heading(text)
        if heading:
            return heading
        compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
        return compact[:240]

    def _first_heading(self, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    def _is_hidden_or_internal(self, name: str) -> bool:
        if name.startswith("."):
            return True
        if name.startswith("_"):
            return True
        if name.endswith(".resolved") or ".resolved." in name:
            return True
        if name.endswith(".metadata.json"):
            return True
        return False

    def _mime_type(self, path: Path) -> str:
        guessed, _ = mimetypes.guess_type(path.name)
        return guessed or "application/octet-stream"

    def _file_kind(self, path: Path) -> str:
        mime = self._mime_type(path)
        if mime.startswith("image/"):
            return "image"
        return "text"

    def _live_cache_dir(self, session_id: str) -> Path:
        path = self.cache_root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _messages_cache_path(self, session_id: str) -> Path:
        return self._live_cache_dir(session_id) / "messages.json"

    def _steps_cache_path(self, session_id: str) -> Path:
        return self._live_cache_dir(session_id) / "steps.json"

    def _legacy_steps_cache_path(self, session_id: str) -> Path:
        return self._live_cache_dir(session_id) / "_live_get_trajectory_steps.json"

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _discover_live_ls_connection(self) -> Dict[str, Any]:
        proc = subprocess.run(["ps", "-eo", "pid,args"], stdout=subprocess.PIPE, text=True, check=False)
        rows = []
        for line in proc.stdout.splitlines():
            if ("language_server_macos_arm" not in line and "language_server_macos_x64" not in line) or "csrf_token" not in line:
                continue
            match = re.match(r"\s*(\d+)\s+(.*)", line)
            if not match:
                continue
            rows.append((int(match.group(1)), match.group(2)))
        if not rows:
            return {"error": "no live language server found"}

        pid, args = self._select_process_row(rows)
        csrf_match = re.search(r"--csrf_token\s+([0-9a-fA-F-]+)", args)
        if not csrf_match:
            return {"error": f"csrf token missing for pid {pid}"}

        lsof = subprocess.run(
            ["lsof", "-Pan", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        ports: List[int] = []
        for line in lsof.stdout.splitlines():
            port_match = re.search(r":(\d+) \(LISTEN\)", line)
            if port_match:
                ports.append(int(port_match.group(1)))

        if not ports:
            return {"error": f"no listening ports for pid {pid}"}

        return {
            "pid": pid,
            "csrf_token": csrf_match.group(1),
            "ports": sorted(set(ports)),
        }

    def _select_process_row(self, rows: List[tuple[int, str]]) -> tuple[int, str]:
        hints = self._workspace_hints()
        for hint in hints:
            for pid, args in rows:
                if hint in args.lower():
                    return pid, args
        return rows[-1]

    def _workspace_hints(self) -> List[str]:
        parts = [part for part in self.repo_root.resolve().parts if part and part != "/"]
        hints: List[str] = []
        if parts:
            hints.append(parts[-1].replace("-", "_").replace(".", "_").lower())
        if len(parts) >= 2:
            hints.append("_".join(parts[-2:]).replace("-", "_").replace(".", "_").lower())
        if len(parts) >= 3:
            hints.append("_".join(parts[-3:]).replace("-", "_").replace(".", "_").lower())
        full = "_".join(parts).replace("-", "_").replace(".", "_").lower()
        if full:
            hints.append(full)
        return list(dict.fromkeys(hints))

    def _ensure_live_ls_connection(self, wait_seconds: float = 12.0) -> Dict[str, Any]:
        conn = self._discover_live_ls_connection()
        if not conn.get("error"):
            return conn

        try:
            subprocess.run(
                ["antigravity", "chat", "-m", "ask", "你好"],
                cwd=str(self.repo_root),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            conn = self._discover_live_ls_connection()
            if not conn.get("error"):
                return conn
            time.sleep(1.0)
        return conn

    def _launch_antigravity_for_session(self, session_id: str) -> None:
        try:
            subprocess.run(
                ["antigravity", "chat", "-m", "ask", "你好"],
                cwd=str(self.repo_root),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _live_ls_rpc(self, method: str, payload: Dict[str, Any]) -> Any:
        session_id = payload.get("cascadeId")
        conn = self._ensure_live_ls_connection()
        if conn.get("error") and isinstance(session_id, str):
            self._launch_antigravity_for_session(session_id)
            conn = self._ensure_live_ls_connection(wait_seconds=20.0)
        if conn.get("error"):
            return conn

        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "X-Codeium-Csrf-Token": str(conn["csrf_token"]),
        }
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        errors: List[str] = []
        for port in conn["ports"]:
            for scheme in ("https", "http"):
                url = f"{scheme}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/{method}"
                try:
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=6, context=ctx if scheme == "https" else None) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                except Exception as exc:
                    errors.append(f"{scheme}:{port} -> {exc}")

        return {"error": "all live LS attempts failed", "details": errors, "discovery": conn}

    def _steps_to_messages(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        messages: List[ChatMessage] = []
        for step in steps:
            step_type = step.get("type") or ""
            metadata = step.get("metadata") or {}
            created_at = metadata.get("createdAt") or ""
            info = metadata.get("sourceTrajectoryStepInfo") or {}
            step_index = info.get("stepIndex")

            if step_type == "CORTEX_STEP_TYPE_USER_INPUT":
                user_input = step.get("userInput") or {}
                content = user_input.get("userResponse") or ""
                attachments = self._extract_item_attachments(user_input.get("items") or [])
                if content:
                    messages.append(ChatMessage("user", content, created_at, step_index, attachments=attachments or None))
                elif attachments:
                    messages.append(ChatMessage("user", "", created_at, step_index, attachments=attachments))
            elif step_type == "CORTEX_STEP_TYPE_NOTIFY_USER":
                notify = step.get("notifyUser") or {}
                content = notify.get("notificationContent") or ""
                if content:
                    messages.append(ChatMessage("assistant", content, created_at, step_index))
            elif step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
                planner = step.get("plannerResponse") or {}
                content = planner.get("modifiedResponse") or planner.get("response") or ""
                thought = planner.get("thinking") or ""
                if content or thought:
                    messages.append(ChatMessage("assistant", content, created_at, step_index, thought=thought))
            elif step_type == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
                error_message = step.get("errorMessage") or {}
                error = error_message.get("error") or {}
                content = error.get("userErrorMessage") or error.get("shortError") or ""
                if content:
                    messages.append(ChatMessage("assistant", content, created_at, step_index))
            elif step_type == "CORTEX_STEP_TYPE_CHECKPOINT":
                checkpoint = step.get("checkpoint") or {}
                intent = checkpoint.get("userIntent") or ""
                if intent:
                    # Checkpoints are usually assistant thoughts
                    messages.append(ChatMessage("assistant", "", created_at, step_index, thought=intent))
            elif step_type == "CORTEX_STEP_TYPE_MESSAGE":
                msg = step.get("message") or {}
                content = msg.get("content") or ""
                role = msg.get("role") or "assistant"
                if content:
                    messages.append(ChatMessage(role, content, created_at, step_index))

        return [asdict(item) for item in messages]

    def _extract_item_attachments(self, items: Sequence[Any]) -> List[Dict[str, str]]:
        attachments: List[Dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            wrapped = item.get("item") if isinstance(item.get("item"), dict) else item
            if not isinstance(wrapped, dict):
                continue
            file_info = wrapped.get("file")
            if not isinstance(file_info, dict):
                continue
            absolute_uri = file_info.get("absoluteUri")
            if not isinstance(absolute_uri, str) or not absolute_uri:
                continue
            parsed = urllib.parse.urlparse(absolute_uri)
            if parsed.scheme != "file":
                continue
            path = urllib.parse.unquote(parsed.path)
            mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            attachments.append(
                {
                    "name": Path(path).name or path,
                    "path": path,
                    "mime_type": mime_type,
                    "kind": "image" if mime_type.startswith("image/") else "file",
                }
            )
        return attachments

    def _attachments_from_paths(self, paths: Sequence[Path]) -> List[Dict[str, str]]:
        attachments: List[Dict[str, str]] = []
        for path in paths:
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            attachments.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "mime_type": mime_type,
                    "kind": "image" if mime_type.startswith("image/") else "file",
                }
            )
        return attachments

    def _user_response_from_prompt_and_paths(self, prompt: str, paths: Sequence[Path]) -> str:
        prefix = "".join(f"@[{path}] " for path in paths)
        return (prefix + prompt).strip()


if __name__ == "__main__":
    store = AntigravitySessionStore()
    print(json.dumps(store.warm_all_messages(), ensure_ascii=False, indent=2))
