#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
import mimetypes
import json
import re
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SessionSummary:
    id: str
    title: str
    updated_at: str
    updated_local: str
    file_count: int
    has_messages: bool


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

    def list_sessions(self) -> List[Dict[str, Any]]:
        sessions: List[SessionSummary] = []
        for pb in sorted(self.conversations_dir.glob("*.pb"), key=lambda p: p.stat().st_mtime, reverse=True):
            session_id = pb.stem
            session_dir = self.brain_dir / session_id
            updated = dt.datetime.fromtimestamp(pb.stat().st_mtime, tz=dt.timezone.utc)
            files = self.list_session_files(session_id)
            sessions.append(
                SessionSummary(
                    id=session_id,
                    title=self._session_title(session_id),
                    updated_at=updated.isoformat(),
                    updated_local=updated.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    file_count=len(files),
                    has_messages=self._messages_cache_path(session_id).exists(),
                )
            )
        return [asdict(item) for item in sessions]

    def session_workspace_path(self, session_id: str) -> Optional[str]:
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

    def list_session_files(self, session_id: str) -> List[Dict[str, Any]]:
        session_dir = self.brain_dir / session_id
        if not session_dir.exists():
            return []
        files: List[SessionFile] = []
        for path in sorted(session_dir.iterdir(), key=lambda p: p.name.lower()):
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
        return [asdict(item) for item in files]

    def get_session_file_content(self, session_id: str, file_name: str) -> Dict[str, Any]:
        allowed = {item["name"] for item in self.list_session_files(session_id)}
        if file_name not in allowed:
            raise FileNotFoundError(f"file not found for session {session_id}: {file_name}")
        path = self.brain_dir / session_id / file_name
        return {
            "session_id": session_id,
            "name": file_name,
            "kind": self._file_kind(path),
            "mime_type": self._mime_type(path),
            "content": path.read_text(encoding="utf-8", errors="replace"),
        }

    def get_session_file_bytes(self, session_id: str, file_name: str) -> Dict[str, Any]:
        allowed = {item["name"] for item in self.list_session_files(session_id)}
        if file_name not in allowed:
            raise FileNotFoundError(f"file not found for session {session_id}: {file_name}")
        path = self.brain_dir / session_id / file_name
        return {
            "session_id": session_id,
            "name": file_name,
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
            if "language_server_macos_arm" not in line or "csrf_token" not in line:
                continue
            match = re.match(r"\s*(\d+)\s+(.*)", line)
            if not match:
                continue
            rows.append((int(match.group(1)), match.group(2)))
        if not rows:
            return {"error": "no live language server found"}

        pid, args = rows[-1]
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

    def _ensure_live_ls_connection(self, wait_seconds: float = 12.0) -> Dict[str, Any]:
        conn = self._discover_live_ls_connection()
        if not conn.get("error"):
            return conn

        try:
            subprocess.run(["open", "-a", "Antigravity"], check=False)
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
        workspace = self.session_workspace_path(session_id)
        try:
            if workspace:
                subprocess.run(
                    ["osascript", "-e", f'tell application "Antigravity" to open POSIX file "{workspace}"'],
                    check=False,
                )
            else:
                subprocess.run(["open", "-a", "Antigravity"], check=False)
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
                if content:
                    messages.append(ChatMessage("user", content, created_at, step_index))
            elif step_type == "CORTEX_STEP_TYPE_NOTIFY_USER":
                notify = step.get("notifyUser") or {}
                content = notify.get("notificationContent") or ""
                if content:
                    messages.append(ChatMessage("assistant", content, created_at, step_index))

        return [asdict(item) for item in messages]


if __name__ == "__main__":
    store = AntigravitySessionStore()
    print(json.dumps(store.warm_all_messages(), ensure_ascii=False, indent=2))
