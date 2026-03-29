#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import cgi
import json
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from store import AntigravitySessionStore


class SessionUICLIClient:
    def __init__(self) -> None:
        self.store_script = Path(__file__).resolve().with_name("store.py")
        self.python = sys.executable

    def _run_json(self, args: list[str]) -> Any:
        proc = subprocess.run(
            [self.python, str(self.store_script), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"store CLI failed: {' '.join(args)}")
        return json.loads(proc.stdout)

    def list_sessions(self) -> Any:
        return self._run_json(["sessions", "list"])

    def list_session_files(self, session_id: str) -> Any:
        return self._run_json(["sessions", "files", session_id])

    def get_session_file_content(self, session_id: str, file_name: str) -> Any:
        return self._run_json(["sessions", "file-content", session_id, file_name])

    def get_session_file_bytes(self, session_id: str, file_name: str) -> Dict[str, Any]:
        payload = self._run_json(["sessions", "file-bytes", session_id, file_name])
        return {"bytes": base64.b64decode(payload["base64"]), "mime_type": payload["mime_type"]}

    def get_session_messages(self, session_id: str, force_refresh: bool = False) -> Any:
        args = ["sessions", "messages", session_id]
        if force_refresh:
            args.append("--refresh")
        return self._run_json(args)

    def get_attachment_bytes(self, path: str) -> Dict[str, Any]:
        payload = self._run_json(["attachment", "bytes", path])
        return {"bytes": base64.b64decode(payload["base64"]), "mime_type": payload["mime_type"]}

    def list_models(self) -> Any:
        return self._run_json(["models"])

    def send_message(self, payload: Dict[str, Any], attachment_paths: list[Path]) -> Any:
        args = [
            "chat",
            "send",
            payload.get("message") or "",
            "--timeout",
            str(float(payload.get("timeout") or 60.0)),
            "--idle-seconds",
            str(float(payload.get("idle_seconds") or 1.5)),
            "--poll",
            str(float(payload.get("poll") or 0.5)),
            "--model",
            str(int(payload.get("model") or 1018)),
        ]
        if payload.get("session_id"):
            args += ["--session-id", str(payload["session_id"])]
        for path in attachment_paths:
            args += ["--attachment", str(path)]
        return self._run_json(args)

    def start_session_send(self, payload: Dict[str, Any], attachment_paths: list[Path]) -> Any:
        args = [
            "chat",
            "start",
            payload.get("message") or "",
            "--timeout",
            str(float(payload.get("timeout") or 60.0)),
            "--idle-seconds",
            str(float(payload.get("idle_seconds") or 1.5)),
            "--poll",
            str(float(payload.get("poll") or 0.5)),
            "--model",
            str(int(payload.get("model") or 1018)),
        ]
        for path in attachment_paths:
            args += ["--attachment", str(path)]
        return self._run_json(args)

    def stream_message(self, payload: Dict[str, Any], attachment_paths: list[Path]):
        args = [
            self.python,
            str(self.store_script),
            "chat",
            "stream",
            payload.get("message") or "",
            "--timeout",
            str(float(payload.get("timeout") or 60.0)),
            "--idle-seconds",
            str(float(payload.get("idle_seconds") or 1.5)),
            "--poll",
            str(float(payload.get("poll") or 0.5)),
            "--model",
            str(int(payload.get("model") or 1018)),
        ]
        if payload.get("session_id"):
            args += ["--session-id", str(payload["session_id"])]
        for path in attachment_paths:
            args += ["--attachment", str(path)]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return proc


class SessionUIHandler(BaseHTTPRequestHandler):
    store = SessionUICLIClient()
    uploader = AntigravitySessionStore()

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, body: bytes, mime_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.end_headers()

    def _write_stream_event(self, payload: Dict[str, Any]) -> None:
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(raw)
        self.wfile.flush()

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _read_form_body(self) -> Dict[str, Any]:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )
        payload: Dict[str, Any] = {"files": []}
        for key in ("message", "session_id", "timeout", "idle_seconds", "poll", "model"):
            if key in form:
                payload[key] = form.getvalue(key)
        if "files" in form:
            entries = form["files"]
            if not isinstance(entries, list):
                entries = [entries]
            payload["files"] = [entry for entry in entries if getattr(entry, "filename", None) and getattr(entry, "file", None)]
        return payload

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        try:
            if parsed.path == "/api/sessions":
                self._send_json(self.store.list_sessions())
                return

            if parsed.path == "/api/models":
                self._send_json(self.store.list_models())
                return

            if len(parts) == 4 and parts[0] == "api" and parts[1] == "sessions" and parts[3] == "files":
                self._send_json(self.store.list_session_files(parts[2]))
                return

            if len(parts) == 5 and parts[0] == "api" and parts[1] == "sessions" and parts[3] == "files":
                file_name = urllib.parse.unquote(parts[4])
                self._send_json(self.store.get_session_file_content(parts[2], file_name))
                return

            if len(parts) == 6 and parts[0] == "api" and parts[1] == "sessions" and parts[3] == "files" and parts[5] == "raw":
                file_name = urllib.parse.unquote(parts[4])
                file_info = self.store.get_session_file_bytes(parts[2], file_name)
                self._send_bytes(file_info["bytes"], file_info["mime_type"])
                return

            if len(parts) == 4 and parts[0] == "api" and parts[1] == "sessions" and parts[3] == "messages":
                query = urllib.parse.parse_qs(parsed.query)
                force_refresh = query.get("refresh", ["0"])[0] == "1"
                self._send_json(self.store.get_session_messages(parts[2], force_refresh=force_refresh))
                return

            if parsed.path == "/api/attachment":
                query = urllib.parse.parse_qs(parsed.query)
                path = query.get("path", [""])[0]
                info = self.store.get_attachment_bytes(path)
                self._send_bytes(info["bytes"], info["mime_type"])
                return

            if parsed.path == "/":
                self._send_html(self._html())
                return

            self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        try:
            if parsed.path == "/api/chat/start":
                content_type = self.headers.get_content_type()
                payload = self._read_form_body() if content_type == "multipart/form-data" else self._read_json_body()
                attachment_paths = [self.uploader.save_uploaded_file(entry.filename, entry.file) for entry in payload.get("files", [])]
                result = self.store.start_session_send(payload, attachment_paths)
                self._send_json(result)
                return

            if parsed.path == "/api/chat/stream":
                content_type = self.headers.get_content_type()
                payload = self._read_form_body() if content_type == "multipart/form-data" else self._read_json_body()
                attachment_paths = [self.uploader.save_uploaded_file(entry.filename, entry.file) for entry in payload.get("files", [])]
                self._send_stream_headers()
                proc = self.store.stream_message(payload, attachment_paths)
                assert proc.stdout is not None
                for line in proc.stdout:
                    if line.strip():
                        self.wfile.write(line.encode("utf-8"))
                        self.wfile.flush()
                return

            if parsed.path == "/api/chat/send":
                content_type = self.headers.get_content_type()
                payload = self._read_form_body() if content_type == "multipart/form-data" else self._read_json_body()
                attachment_paths = [self.uploader.save_uploaded_file(entry.filename, entry.file) for entry in payload.get("files", [])]
                result = self.store.send_message(payload, attachment_paths)
                self._send_json(result)
                return

            self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _html(self) -> str:
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Antigravity Sessions</title>
  <style>
    :root {
      --bg: #f7f7f8;
      --sidebar: #171717;
      --text: #111827;
      --muted: #6b7280;
      --muted-dark: #a3a3a3;
      --white: #ffffff;
      --surface: #ffffff;
      --line: #e5e7eb;
      --shadow: 0 12px 30px rgba(0, 0, 0, 0.08);
      --mono: "SF Mono", "JetBrains Mono", monospace;
      --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); }
    body { overflow: hidden; }
    .app { display: grid; grid-template-columns: 308px 280px minmax(0, 1fr); height: 100vh; }
    .sidebar {
      background: linear-gradient(180deg, #1b1b1b 0%, #111111 100%);
      color: #f5f5f5;
      border-right: 1px solid rgba(255,255,255,0.06);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .sidebar-head, .files-head { padding: 16px 14px 12px; border-bottom: 1px solid var(--line); background: #fff; color: var(--text); }
    .sidebar-head { background: transparent; color: #fff; border-bottom-color: rgba(255,255,255,0.08); }
    .sidebar-head h1, .files-head h2 { margin: 0 0 6px 0; font-size: 14px; font-weight: 700; }
    .sidebar-head p, .files-head p { margin: 0; font-size: 12px; line-height: 1.45; }
    .sidebar-head p { color: var(--muted-dark); }
    .files-head p { color: var(--muted); }
    .search-wrap { padding: 12px 12px 0; }
    .search {
      width: 100%; border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.06);
      color: #fff; border-radius: 10px; padding: 11px 12px; outline: none; font-size: 13px;
    }
    .search::placeholder { color: #9ca3af; }
    .session-list, .file-list { flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 6px; min-height: 0; }
    .files { border-right: 1px solid var(--line); background: #fbfbfc; display: flex; flex-direction: column; min-height: 0; }
    .session, .file-item {
      border-radius: 12px; padding: 12px 12px 11px; cursor: pointer; border: 1px solid transparent;
      background: transparent; transition: background .12s ease, border-color .12s ease;
    }
    .session:hover { background: rgba(255,255,255,0.06); }
    .file-item:hover { background: #f2f4f7; }
    .session.active { background: rgba(255,255,255,0.10); border-color: rgba(255,255,255,0.12); }
    .file-item.active { background: #eef2ff; border-color: #c7d2fe; }
    .session-title, .file-name { margin: 0 0 5px 0; font-size: 12px; font-weight: 700; line-height: 1.35; }
    .session-title { color: #fafafa; }
    .file-name { color: #111827; word-break: break-word; }
    .session-meta, .session-summary, .file-meta, .file-summary { margin: 0; font-size: 11px; line-height: 1.45; }
    .session-meta, .session-summary { color: var(--muted-dark); }
    .file-meta, .file-summary { color: var(--muted); }
    .session-summary, .file-summary { margin-top: 6px; display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }
    .session-summary { -webkit-line-clamp: 2; }
    .session-workspace {
      margin: 6px 0 0;
      font-size: 11px;
      line-height: 1.45;
      color: #d1d5db;
      font-family: var(--mono);
      word-break: break-all;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      overflow: hidden;
    }
    .file-summary { -webkit-line-clamp: 3; }
    .chat { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-width: 0; min-height: 0; }
    .topbar { padding: 18px 24px 14px; border-bottom: 1px solid var(--line); background: rgba(255,255,255,0.78); backdrop-filter: blur(12px); }
    .topbar h2 { margin: 0; font-size: 22px; line-height: 1.2; font-weight: 650; }
    .topbar p { margin: 8px 0 0; font-size: 13px; color: var(--muted); line-height: 1.5; }
    .conversation { overflow: auto; padding: 22px 0 32px; min-height: 0; }
    .conversation-inner { width: min(980px, calc(100% - 28px)); margin: 0 auto; display: flex; flex-direction: column; gap: 18px; }
    .msg { display: flex; gap: 14px; align-items: flex-start; }
    .avatar { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; font-size: 12px; font-weight: 700; flex: 0 0 auto; }
    .avatar.user { background: #111827; color: #fff; }
    .avatar.assistant { background: #19c37d; color: #083d2d; }
    .bubble { flex: 1; min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); overflow: hidden; }
    .bubble-header { padding: 14px 16px 10px; border-bottom: 1px solid #eef0f3; background: linear-gradient(180deg, #ffffff 0%, #fafafa 100%); }
    .bubble-role { font-size: 12px; font-weight: 700; text-transform: uppercase; color: #111827; margin: 0 0 4px 0; }
    .bubble-meta, .bubble-summary { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.45; white-space: pre-wrap; }
    .bubble-summary { margin-top: 8px; font-size: 14px; color: #374151; }
    .message-body {
      padding: 16px;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.65;
      font-size: 14px;
      color: #1f2937;
    }
    .message-body p { margin: 0 0 12px; }
    .message-body p:last-child { margin-bottom: 0; }
    .message-body h1, .message-body h2, .message-body h3, .message-body h4 { margin: 0 0 12px; line-height: 1.3; }
    .message-body ul, .message-body ol { margin: 0 0 12px 20px; padding: 0; }
    .message-body li { margin: 4px 0; }
    .message-body blockquote {
      margin: 0 0 12px;
      padding: 8px 12px;
      border-left: 3px solid #cbd5e1;
      background: #f8fafc;
      color: #334155;
    }
    .message-body pre {
      margin: 0 0 12px;
      border-radius: 12px;
      background: #0f172a;
      color: #e5edf7;
      overflow: auto;
      padding: 14px;
      white-space: pre-wrap;
    }
    .message-body code {
      font-family: var(--mono);
      background: #eef2f7;
      border-radius: 6px;
      padding: 1px 5px;
      font-size: 12px;
    }
    .message-body pre code { background: transparent; padding: 0; color: inherit; }
    .message-body a { color: #2563eb; text-decoration: none; }
    .message-body table {
      width: 100%;
      border-collapse: collapse;
      margin: 0 0 12px;
      font-size: 13px;
    }
    .message-body th, .message-body td {
      border: 1px solid #dbe1ea;
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
    }
    .message-body th {
      background: #f8fafc;
      font-weight: 700;
    }
    .attachments { display: grid; gap: 8px; padding: 0 16px 16px; }
    .attachment-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 8px 10px;
      background: #f8fafc;
      color: #334155;
      font-size: 12px;
      text-decoration: none;
      word-break: break-all;
    }
    .attachment-image {
      max-width: min(100%, 340px);
      border-radius: 12px;
      border: 1px solid #e5e7eb;
      background: #fff;
      cursor: zoom-in;
    }
    .composer-preview-grid {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 2px;
    }
    .composer-preview-card {
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 8px;
      background: #fff;
      display: flex;
      flex-direction: column;
      gap: 6px;
      max-width: 180px;
    }
    .composer-preview-card img {
      width: 160px;
      height: 100px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid #eef2f7;
      background: #f8fafc;
    }
    .composer-preview-name {
      font-size: 11px;
      color: #475569;
      word-break: break-all;
    }
    .code-wrap { margin: 0; background: #0f172a; color: #e5edf7; font-family: var(--mono); font-size: 12px; line-height: 1.6; overflow: auto; white-space: pre-wrap; word-break: break-word; }
    .code-wrap code { display: block; padding: 16px; font-family: inherit; }
    .thought-wrap { margin: 8px 16px; font-size: 11px; }
    .thought-wrap summary { cursor: pointer; color: var(--muted-dark); font-weight: 500; padding: 4px 0; outline: none; }
    .thought-wrap summary:hover { color: #fff; background: rgba(255,255,255,0.05); }
    .thought-content { margin-top: 8px; white-space: pre-wrap; font-family: var(--mono); color: #94a3b8; line-height: 1.5; padding: 12px; background: rgba(0,0,0,0.2); border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
    .bubble.user .thought-content { color: #64748b; background: #f8fafc; }
    .file-panel {
      width: min(1080px, calc(100% - 28px));
      margin: 0 auto;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 20px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .file-panel-head {
      padding: 16px 18px 12px;
      border-bottom: 1px solid #eef0f3;
      background: linear-gradient(180deg, #ffffff 0%, #fafafa 100%);
    }
    .file-panel-head h3 {
      margin: 0 0 6px 0;
      font-size: 15px;
      font-weight: 700;
      word-break: break-word;
    }
    .file-panel-head p {
      margin: 0;
      font-size: 12px;
      color: var(--muted);
      line-height: 1.45;
    }
    .file-panel-body {
      padding: 18px;
      background: #fff;
    }
    .image-frame {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      padding: 12px;
      background: #fcfcfd;
      cursor: zoom-in;
      max-width: 100%;
      overflow: auto;
    }
    .image-frame img {
      max-width: min(100%, 820px);
      height: auto;
      display: block;
      border-radius: 10px;
    }
    .file-code {
      margin: 0;
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      background: #0f172a;
      color: #e5edf7;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.6;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .file-code code {
      display: block;
      padding: 16px;
      font-family: inherit;
    }
    .lightbox {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.78);
      display: none;
      align-items: stretch;
      justify-content: stretch;
      z-index: 9999;
      padding: 0;
    }
    .lightbox.open { display: flex; }
    .lightbox-shell {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      width: 100%;
      height: 100%;
    }
    .lightbox-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 18px;
      background: rgba(17, 24, 39, 0.92);
      border-bottom: 1px solid rgba(255,255,255,0.08);
      color: #fff;
    }
    .lightbox-title {
      font-size: 13px;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      padding-right: 12px;
    }
    .lightbox-actions {
      display: flex;
      gap: 8px;
      flex: 0 0 auto;
    }
    .lightbox-btn {
      border: 1px solid rgba(255,255,255,0.16);
      background: rgba(255,255,255,0.08);
      color: #fff;
      border-radius: 10px;
      padding: 8px 12px;
      font-size: 12px;
      cursor: pointer;
    }
    .lightbox-stage {
      overflow: auto;
      padding: 20px;
      display: flex;
      align-items: flex-start;
      justify-content: center;
    }
    .lightbox-stage img {
      display: block;
      border-radius: 14px;
      box-shadow: 0 24px 80px rgba(0,0,0,0.35);
      background: #fff;
      max-width: none;
      height: auto;
    }
    .lightbox-stage.fit-width img {
      width: min(1200px, calc(100vw - 80px));
      max-width: 100%;
    }
    .lightbox-stage.actual-size img {
      width: auto;
    }
    .empty { width: min(760px, calc(100% - 28px)); margin: 40px auto; padding: 28px; text-align: center; background: #fff; border: 1px solid var(--line); border-radius: 20px; color: var(--muted); box-shadow: var(--shadow); }
    .composer {
      border-top: 1px solid var(--line);
      background: rgba(255,255,255,0.92);
      backdrop-filter: blur(12px);
      padding: 14px 18px 16px;
      display: grid;
      gap: 10px;
    }
    .composer-input {
      width: 100%;
      min-height: 88px;
      max-height: 220px;
      resize: vertical;
      border: 1px solid #d1d5db;
      border-radius: 14px;
      padding: 12px 14px;
      font: inherit;
      font-size: 14px;
      outline: none;
      background: #fff;
    }
    .composer-input:focus {
      border-color: #94a3b8;
      box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.14);
    }
    .composer-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .status-line {
      font-size: 12px;
      color: var(--muted);
      min-height: 18px;
    }
    .status-line.error { color: #b91c1c; }
    .action-group { display: flex; gap: 8px; }
    .model-select {
      border: 1px solid #d1d5db;
      border-radius: 10px;
      padding: 8px 10px;
      background: #fff;
      font-size: 12px;
      color: #111827;
    }
    .file-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .file-label {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px dashed #cbd5e1;
      border-radius: 10px;
      padding: 8px 12px;
      font-size: 12px;
      cursor: pointer;
      background: #fff;
    }
    .file-input {
      position: absolute;
      width: 1px;
      height: 1px;
      opacity: 0;
      pointer-events: none;
      overflow: hidden;
    }
    .selected-file-list { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
    .file-pill { border: 1px solid #e5e7eb; border-radius: 999px; padding: 4px 8px; background: #fff; }
    .btn {
      border: 1px solid #d1d5db;
      background: #fff;
      color: #111827;
      border-radius: 12px;
      padding: 10px 14px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }
    .btn.primary {
      background: #111827;
      color: #fff;
      border-color: #111827;
    }
    .btn:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
    @media (max-width: 900px) {
      .app { grid-template-columns: 1fr; grid-template-rows: 32vh 26vh minmax(0, 1fr); }
      .sidebar { border-right: none; border-bottom: 1px solid rgba(255,255,255,0.08); }
      .files { border-right: none; border-bottom: 1px solid var(--line); }
      .conversation-inner { width: calc(100% - 16px); }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="sidebar-head">
        <h1>Antigravity Sessions</h1>
        <p>Sessions on the left, files in the middle, and the conversation on the right.</p>
      </div>
      <div class="search-wrap"><input id="search" class="search" placeholder="Search by session id or title"></div>
      <div id="session-list" class="session-list"></div>
    </aside>
    <section class="files">
      <div class="files-head">
        <h2>Files</h2>
        <p>Only session files are shown here. Select a file to inspect its content.</p>
      </div>
      <div id="file-list" class="file-list"></div>
    </section>
    <main class="chat">
      <div id="topbar" class="topbar"></div>
      <div id="conversation" class="conversation"></div>
      <div class="composer">
        <textarea id="composer-input" class="composer-input" placeholder="Type a message. If no session is selected, a new chat will start automatically. If a session is selected, the message will continue that chat."></textarea>
        <div class="file-row">
          <button id="file-trigger-btn" class="file-label" type="button" onclick="document.getElementById('file-input').click()">Add image or file</button>
          <input id="file-input" class="file-input" type="file" multiple onchange="renderSelectedFiles()" aria-hidden="true">
          <div id="selected-file-list" class="selected-file-list"></div>
        </div>
        <div id="composer-preview-grid" class="composer-preview-grid"></div>
        <div class="composer-actions">
          <div id="composer-status" class="status-line">Ready.</div>
          <div class="action-group">
            <select id="model-select" class="model-select" title="Model">
              <option value="1018">Gemini 3 Flash</option>
            </select>
            <button id="new-chat-btn" class="btn" type="button" onclick="sendChat(true)">Send as new chat</button>
            <button id="send-btn" class="btn primary" type="button" onclick="sendChat(false)">Send</button>
          </div>
        </div>
      </div>
    </main>
  </div>
  <div id="lightbox" class="lightbox">
    <div class="lightbox-shell">
      <div class="lightbox-toolbar">
        <div id="lightbox-title" class="lightbox-title"></div>
        <div class="lightbox-actions">
          <button id="lightbox-toggle" class="lightbox-btn" type="button">Actual size</button>
          <button id="lightbox-close" class="lightbox-btn" type="button">Close</button>
        </div>
      </div>
      <div id="lightbox-stage" class="lightbox-stage fit-width">
        <img id="lightbox-image" alt="">
      </div>
    </div>
  </div>
  <script>
    const state = { sessions: [], filtered: [], activeSessionId: null, activeFile: null, files: [], messages: [], sending: false };
    const imageState = { mode: 'fit-width' };
    let autoRefreshTimer = null;

    async function fetchJSON(url) {
      const res = await fetch(url);
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"]/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
    }

    function snippet(value, maxLen = 160) {
      const text = String(value || '').replace(/\\s+/g, ' ').trim();
      return text.length > maxLen ? text.slice(0, maxLen) + '…' : text;
    }

    function renderInlineMarkdown(text) {
      let html = escapeHtml(text);
      html = html.replace(/!\\[([^\\]]*)\\]\\(([^)]+)\\)/g, '<img class="attachment-image" alt="$1" src="$2">');
      html = html.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
      html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
      html = html.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
      html = html.replace(/(^|\\W)\\*([^*]+)\\*(?=\\W|$)/g, '$1<em>$2</em>');
      return html;
    }

    function renderMarkdown(text) {
      const source = String(text || '').replace(/\\r\\n/g, '\\n');
      if (!source.trim()) return '';

      const codeBlocks = [];
      const normalized = source.replace(/```([\\w-]*)\\n([\\s\\S]*?)```/g, (_, lang, code) => {
        const token = `@@CODE_${codeBlocks.length}@@`;
        codeBlocks.push(`<pre><code>${escapeHtml(code)}</code></pre>`);
        return token;
      });

      const blocks = normalized.split(/\\n{2,}/).map((block) => block.trim()).filter(Boolean);
      const rendered = blocks.map((block) => {
        const codeMatch = block.match(/^@@CODE_(\\d+)@@$/);
        if (codeMatch) return codeBlocks[Number(codeMatch[1])];

        const heading = block.match(/^(#{1,4})\\s+(.+)$/);
        if (heading) {
          const level = heading[1].length;
          return `<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`;
        }

        if (block.split('\\n').every((line) => /^>\\s?/.test(line))) {
          const inner = block.split('\\n').map((line) => renderInlineMarkdown(line.replace(/^>\\s?/, ''))).join('<br>');
          return `<blockquote>${inner}</blockquote>`;
        }

        if (block.split('\\n').every((line) => /^[-*]\\s+/.test(line))) {
          const items = block.split('\\n').map((line) => `<li>${renderInlineMarkdown(line.replace(/^[-*]\\s+/, ''))}</li>`).join('');
          return `<ul>${items}</ul>`;
        }

        if (block.split('\\n').every((line) => /^\\d+\\.\\s+/.test(line))) {
          const items = block.split('\\n').map((line) => `<li>${renderInlineMarkdown(line.replace(/^\\d+\\.\\s+/, ''))}</li>`).join('');
          return `<ol>${items}</ol>`;
        }

        const lines = block.split('\\n');
        const looksLikeTable = lines.length >= 2
          && lines[0].includes('|')
          && lines[1].includes('|')
          && /^\\s*\\|?\\s*:?-{3,}/.test(lines[1]);
        if (looksLikeTable) {
          const splitRow = (row) => row.trim().replace(/^\\||\\|$/g, '').split('|').map((cell) => cell.trim());
          const headers = splitRow(lines[0]);
          const rows = lines.slice(2).filter(Boolean).map(splitRow);
          const head = `<tr>${headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join('')}</tr>`;
          const body = rows.map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>`).join('');
          return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
        }

        return `<p>${block.split('\\n').map(renderInlineMarkdown).join('<br>')}</p>`;
      }).join('');

      return rendered.replace(/@@CODE_(\\d+)@@/g, (_, index) => codeBlocks[Number(index)]);
    }

    function renderAttachments(msg) {
      const attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
      if (!attachments.length) return '';
      const body = attachments.map((item) => {
        const href = `/api/attachment?path=${encodeURIComponent(item.path)}`;
        if (item.kind === 'image') {
          return `<img class="attachment-image" data-lightbox-src="${href}" data-lightbox-title="${escapeHtml(item.name)}" src="${href}" alt="${escapeHtml(item.name)}">`;
        }
        return `<a class="attachment-chip" href="${href}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.name)}</a>`;
      }).join('');
      return `<div class="attachments">${body}</div>`;
    }

    function setStatus(text, isError = false) {
      const el = document.getElementById('composer-status');
      el.textContent = text;
      el.className = isError ? 'status-line error' : 'status-line';
    }

    function setSending(sending) {
      state.sending = sending;
      document.getElementById('send-btn').disabled = sending;
      document.getElementById('new-chat-btn').disabled = sending;
      document.getElementById('composer-input').disabled = sending;
      document.getElementById('file-input').disabled = sending;
      document.getElementById('model-select').disabled = sending;
    }

    async function loadModels() {
      const models = await fetchJSON('/api/models');
      const select = document.getElementById('model-select');
      select.innerHTML = models.map((item) => `
        <option value="${escapeHtml(String(item.id))}" ${item.default ? 'selected' : ''}>${escapeHtml(item.label)}</option>
      `).join('');
    }

    function stopAutoRefresh() {
      if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
      }
    }

    function startAutoRefresh(sessionId, durationMs = 45000, intervalMs = 1500) {
      stopAutoRefresh();
      const startedAt = Date.now();
      autoRefreshTimer = setInterval(async () => {
        if (Date.now() - startedAt > durationMs) {
          stopAutoRefresh();
          return;
        }
        if (state.activeSessionId !== sessionId) return;
        try {
          state.files = await fetchJSON(`/api/sessions/${encodeURIComponent(sessionId)}/files`);
          state.messages = await fetchJSON(`/api/sessions/${encodeURIComponent(sessionId)}/messages?refresh=1`);
          renderFileList();
          await renderConversation();
          const last = state.messages[state.messages.length - 1];
          if (last && last.role === 'assistant' && last.content) {
            setStatus(`Reply received · ${sessionId}`);
            stopAutoRefresh();
          }
        } catch (_) {
          // ignore transient refresh errors during background send
        }
      }, intervalMs);
    }

    function selectedFiles() {
      return Array.from(document.getElementById('file-input').files || []);
    }

    function renderSelectedFiles() {
      const root = document.getElementById('selected-file-list');
      const preview = document.getElementById('composer-preview-grid');
      const files = selectedFiles();
      root.innerHTML = files.map((file) => `<span class="file-pill">${escapeHtml(file.name)}</span>`).join('');
      preview.innerHTML = files.map((file) => {
        if (file.type && file.type.startsWith('image/')) {
          return `<div class="composer-preview-card"><img src="${URL.createObjectURL(file)}" alt="${escapeHtml(file.name)}"><div class="composer-preview-name">${escapeHtml(file.name)}</div></div>`;
        }
        return `<div class="composer-preview-card"><div class="composer-preview-name">${escapeHtml(file.name)}</div></div>`;
      }).join('');
    }

    function optimisticUserMessage(message) {
      return {
        role: 'user',
        content: message,
        created_at: new Date().toISOString(),
        step_index: null,
        thought: null,
        attachments: null,
      };
    }

    function renderSessionList() {
      const root = document.getElementById('session-list');
      root.innerHTML = state.filtered.map((item) => `
        <article class="session ${item.id === state.activeSessionId ? 'active' : ''}" data-id="${item.id}">
          <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <h2 class="session-title" style="flex: 1;">${escapeHtml(item.title)}</h2>
            <button class="refresh-btn" data-id="${item.id}" title="Force refresh message cache" style="background: none; border: none; color: #666; cursor: pointer; padding: 2px 4px; font-size: 14px;">↻</button>
          </div>
          <p class="session-meta">${escapeHtml(item.updated_local)} · ${item.file_count} files</p>
          <p class="session-summary">${escapeHtml(item.id)}</p>
          ${item.workspace_path ? `<p class="session-workspace" title="${escapeHtml(item.workspace_path)}">${escapeHtml(item.workspace_path)}</p>` : ''}
        </article>
      `).join('');
      for (const el of root.querySelectorAll('.session')) {
        el.addEventListener('click', (e) => {
          if (e.target.classList.contains('refresh-btn')) return;
          selectSession(el.dataset.id);
        });
      }
      for (const btn of root.querySelectorAll('.refresh-btn')) {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          refreshSession(btn.dataset.id);
        });
      }
    }

    function renderFileList() {
      const root = document.getElementById('file-list');
      root.innerHTML = state.files.map((item) => `
        <article class="file-item ${item.name === state.activeFile ? 'active' : ''}" data-name="${item.name}">
          <h3 class="file-name">${escapeHtml(item.name)}</h3>
          <p class="file-meta">${escapeHtml(item.updated_at)} · ${escapeHtml(String(item.size))} bytes</p>
          <p class="file-summary">${escapeHtml(snippet(item.summary))}</p>
        </article>
      `).join('');
      for (const el of root.querySelectorAll('.file-item')) {
        el.addEventListener('click', () => {
          state.activeFile = el.dataset.name;
          renderConversation();
          renderFileList();
        });
      }
    }

    function renderTopbar() {
      const top = document.getElementById('topbar');
      const session = state.sessions.find((s) => s.id === state.activeSessionId);
      if (!session) {
        top.innerHTML = '<h2>Antigravity Sessions</h2><p>Select a session to inspect its content.</p>';
        return;
      }
      top.innerHTML = `
        <h2>${escapeHtml(session.title)}</h2>
        <p>${escapeHtml(session.id)}</p>
      `;
    }

    async function renderConversation() {
      const root = document.getElementById('conversation');
      if (!state.activeSessionId) {
        root.innerHTML = '<div class="empty">Select a session.</div>';
        return;
      }

      if (!state.activeFile) {
        root.innerHTML = `
          <div class="conversation-inner">
            ${state.messages.length ? state.messages.map((msg) => `
              <section class="msg ${msg.role === 'user' ? 'user' : 'assistant'}">
                <div class="avatar ${msg.role === 'assistant' ? 'assistant' : 'user'}">${msg.role === 'assistant' ? 'AG' : 'ME'}</div>
                <div class="bubble ${msg.role === 'user' ? 'user' : 'assistant'}">
                  <div class="bubble-header">
                    <p class="bubble-role">${escapeHtml(msg.role)}</p>
                    <p class="bubble-meta">${escapeHtml(msg.created_at || '')}${msg.step_index !== null && msg.step_index !== undefined ? ` · step ${escapeHtml(String(msg.step_index))}` : ''}</p>
                  </div>
                  ${msg.thought ? `
                    <details class="thought-wrap">
                      <summary>Thought</summary>
                      <div class="thought-content">${escapeHtml(msg.thought)}</div>
                    </details>
                  ` : ''}
                  ${msg.content ? `<div class="message-body">${renderMarkdown(msg.content)}</div>` : ''}
                  ${renderAttachments(msg)}
                </div>
              </section>
            `).join('') : '<div class="empty">No recoverable chat history is available for this session yet.</div>'}
          </div>
        `;
        for (const image of root.querySelectorAll('.attachment-image[data-lightbox-src]')) {
          image.addEventListener('click', () => openLightbox(image.dataset.lightboxSrc, image.dataset.lightboxTitle || image.alt || 'image'));
        }
        return;
      }

      const file = await fetchJSON(`/api/sessions/${encodeURIComponent(state.activeSessionId)}/files/${encodeURIComponent(state.activeFile)}`);
      if (file.kind === 'image') {
        const rawUrl = `/api/sessions/${encodeURIComponent(state.activeSessionId)}/files/${encodeURIComponent(state.activeFile)}/raw`;
        root.innerHTML = `
          <div class="file-panel">
            <div class="file-panel-head">
              <h3>${escapeHtml(file.name)}</h3>
              <p>${escapeHtml(file.mime_type || 'image')}</p>
            </div>
            <div class="file-panel-body">
              <div class="image-frame" id="image-frame">
                <img src="${rawUrl}" alt="${escapeHtml(file.name)}" />
              </div>
            </div>
          </div>
        `;
        const frame = document.getElementById('image-frame');
        if (frame) {
          frame.addEventListener('click', () => openLightbox(rawUrl, file.name));
        }
        return;
      }
      root.innerHTML = `
        <div class="file-panel">
          <div class="file-panel-head">
            <h3>${escapeHtml(file.name)}</h3>
            <p>${escapeHtml(file.mime_type || 'text/plain')}</p>
          </div>
          <div class="file-panel-body">
            <pre class="file-code"><code>${escapeHtml(file.content)}</code></pre>
          </div>
        </div>
      `;
    }

    function openLightbox(src, alt) {
      const box = document.getElementById('lightbox');
      const stage = document.getElementById('lightbox-stage');
      const image = document.getElementById('lightbox-image');
      const title = document.getElementById('lightbox-title');
      const toggle = document.getElementById('lightbox-toggle');
      imageState.mode = 'fit-width';
      stage.className = 'lightbox-stage fit-width';
      image.src = src;
      image.alt = alt || '';
      title.textContent = alt || '';
      toggle.textContent = 'Actual size';
      box.classList.add('open');
    }

    function closeLightbox() {
      const box = document.getElementById('lightbox');
      const stage = document.getElementById('lightbox-stage');
      const image = document.getElementById('lightbox-image');
      image.src = '';
      image.alt = '';
      stage.scrollTop = 0;
      stage.scrollLeft = 0;
      box.classList.remove('open');
    }

    function toggleLightboxMode() {
      const stage = document.getElementById('lightbox-stage');
      const toggle = document.getElementById('lightbox-toggle');
      if (imageState.mode === 'fit-width') {
        imageState.mode = 'actual-size';
        stage.className = 'lightbox-stage actual-size';
        toggle.textContent = 'Fit width';
      } else {
        imageState.mode = 'fit-width';
        stage.className = 'lightbox-stage fit-width';
        toggle.textContent = 'Actual size';
      }
    }

    async function selectSession(id, forceRefresh = false) {
      if (state.activeSessionId !== id) {
        stopAutoRefresh();
      }
      state.activeSessionId = id;
      state.files = await fetchJSON(`/api/sessions/${encodeURIComponent(id)}/files`);
      state.messages = await fetchJSON(`/api/sessions/${encodeURIComponent(id)}/messages${forceRefresh ? '?refresh=1' : ''}`);
      state.activeFile = null;
      renderSessionList();
      renderFileList();
      renderTopbar();
      renderConversation();
      history.replaceState(null, '', `/#${id}`);
    }

    async function refreshSession(id) {
        const btn = document.querySelector(`.refresh-btn[data-id="${id}"]`);
        if (btn) btn.style.opacity = '0.5';
        try {
            await selectSession(id, true);
            setStatus(`Refreshed ${id}`);
        } finally {
            if (btn) btn.style.opacity = '1';
        }
    }

    async function sendChat(forceNew = false) {
      const input = document.getElementById('composer-input');
      const message = input.value.trim();
      if ((!message && selectedFiles().length === 0) || state.sending) return;

      setSending(true);
      setStatus(forceNew ? 'Creating a new chat and sending…' : 'Sending…');

      try {
        const form = new FormData();
        form.append('message', message);
        if (!forceNew && state.activeSessionId) form.append('session_id', state.activeSessionId);
        form.append('timeout', '60');
        form.append('idle_seconds', '1.5');
        form.append('poll', '0.5');
        form.append('model', document.getElementById('model-select').value);
        for (const file of selectedFiles()) {
          form.append('files', file);
        }

        const endpoint = '/api/chat/stream';
        const res = await fetch(endpoint, {
          method: 'POST',
          body: form
        });
        if (!res.ok || !res.body) {
          throw new Error(await res.text());
        }

        input.value = '';
        document.getElementById('file-input').value = '';
        renderSelectedFiles();

        if (!forceNew && state.activeSessionId) {
          state.messages = [...state.messages, optimisticUserMessage(message)];
          await renderConversation();
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.trim()) continue;
            const event = JSON.parse(line);

            if (event.type === 'session') {
              state.sessions = await fetchJSON('/api/sessions');
              if (event.session && !state.sessions.find((item) => item.id === event.session_id)) {
                state.sessions.unshift(event.session);
              }
              state.filtered = [...state.sessions];
              if (event.created || !state.activeSessionId) {
                await selectSession(event.session_id);
                setStatus(`Created session · ${event.session_id}`);
              } else {
                state.activeSessionId = event.session_id;
                history.replaceState(null, '', `/#${event.session_id}`);
                renderSessionList();
                renderTopbar();
                await renderConversation();
                setStatus(`Connected session · ${event.session_id}`);
              }
              continue;
            }

            if (event.type === 'delta') {
              const existing = state.messages.find((item) => item.role === 'assistant' && item._streaming);
              if (existing) {
                existing.content = event.full;
              } else {
                state.messages.push({
                  role: 'assistant',
                  content: event.full,
                  created_at: '',
                  step_index: null,
                  thought: null,
                  attachments: null,
                  _streaming: true,
                });
              }
              await renderConversation();
              setStatus(`Receiving reply · ${event.session_id}`);
              continue;
            }

            if (event.type === 'done') {
              state.messages = event.messages || [];
              state.sessions = await fetchJSON('/api/sessions');
              state.filtered = [...state.sessions];
              state.files = await fetchJSON(`/api/sessions/${encodeURIComponent(event.session_id)}/files`);
              renderSessionList();
              renderFileList();
              renderTopbar();
              await renderConversation();
              history.replaceState(null, '', `/#${event.session_id}`);
              setStatus(`Reply received · ${event.session_id}`);
            }
          }
        }
      } catch (err) {
        setStatus(String(err), true);
      } finally {
        setSending(false);
      }
    }

    function applySearch(query) {
      const q = query.trim().toLowerCase();
      state.filtered = state.sessions.filter((item) => {
        if (!q) return true;
        return [item.id, item.title].join('\\n').toLowerCase().includes(q);
      });
      renderSessionList();
    }

    async function main() {
      document.getElementById('lightbox-close').addEventListener('click', closeLightbox);
      document.getElementById('lightbox-toggle').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleLightboxMode();
      });
      document.getElementById('lightbox').addEventListener('click', (e) => {
        if (e.target.id === 'lightbox') closeLightbox();
      });
      await loadModels();
      state.sessions = await fetchJSON('/api/sessions');
      state.filtered = [...state.sessions];
      document.getElementById('search').addEventListener('input', (e) => applySearch(e.target.value));
      document.getElementById('file-trigger-btn').addEventListener('click', () => {
        if (!state.sending) {
          document.getElementById('file-input').click();
        }
      });
      document.getElementById('send-btn').addEventListener('click', () => sendChat(false));
      document.getElementById('new-chat-btn').addEventListener('click', () => sendChat(true));
      document.getElementById('file-input').addEventListener('change', renderSelectedFiles);
      document.getElementById('composer-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendChat(false);
        }
      });
      renderSessionList();
      const preferred = decodeURIComponent((location.hash || '').replace(/^#/, ''));
      const target = state.sessions.find((item) => item.id === preferred) || state.sessions[0];
      if (target) await selectSession(target.id);
      else renderTopbar();
    }

    main().catch((err) => {
      document.getElementById('conversation').innerHTML = `<div class="empty">${escapeHtml(String(err))}</div>`;
    });
  </script>
</body>
</html>
"""


class SessionUIApp:
    def __init__(self, store: Optional[SessionUICLIClient] = None) -> None:
        self.store = store or SessionUICLIClient()

    def warm_messages_in_background(self) -> None:
        def _runner() -> None:
            result = self.store._run_json(["cache", "warm"])
            print("[warm-all]", json.dumps(result, ensure_ascii=False))

        threading.Thread(target=_runner, daemon=True).start()

    def serve(self, host: str = "127.0.0.1", port: int = 8766, open_browser: bool = True) -> None:
        SessionUIHandler.store = self.store
        SessionUIHandler.uploader = AntigravitySessionStore()
        server = ThreadingHTTPServer((host, port), SessionUIHandler)
        url = f"http://{host}:{port}/"
        self.warm_messages_in_background()
        print(f"Session UI running at {url}")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity sessions UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    SessionUIApp().serve(host=args.host, port=args.port, open_browser=args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
