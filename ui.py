#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from store import AntigravitySessionStore


class SessionUIHandler(BaseHTTPRequestHandler):
    store = AntigravitySessionStore()

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

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        try:
            if parsed.path == "/api/sessions":
                self._send_json(self.store.list_sessions())
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
                self._send_json(self.store.get_session_messages(parts[2]))
                return

            if parsed.path == "/":
                self._send_html(self._html())
                return

            self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _html(self) -> str:
        return """<!doctype html>
<html lang="zh-CN">
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
    .file-summary { -webkit-line-clamp: 3; }
    .chat { display: grid; grid-template-rows: auto minmax(0, 1fr); min-width: 0; min-height: 0; }
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
    .code-wrap { margin: 0; background: #0f172a; color: #e5edf7; font-family: var(--mono); font-size: 12px; line-height: 1.6; overflow: auto; white-space: pre-wrap; word-break: break-word; }
    .code-wrap code { display: block; padding: 16px; font-family: inherit; }
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
        <p>左侧 session，中间文件列表，右侧默认展示按角色分组的聊天记录。</p>
      </div>
      <div class="search-wrap"><input id="search" class="search" placeholder="搜索 session id、标题"></div>
      <div id="session-list" class="session-list"></div>
    </aside>
    <section class="files">
      <div class="files-head">
        <h2>Files</h2>
        <p>只展示 session 文件。右侧默认显示聊天记录，点击文件后显示文件内容。</p>
      </div>
      <div id="file-list" class="file-list"></div>
    </section>
    <main class="chat">
      <div id="topbar" class="topbar"></div>
      <div id="conversation" class="conversation"></div>
    </main>
  </div>
  <div id="lightbox" class="lightbox">
    <div class="lightbox-shell">
      <div class="lightbox-toolbar">
        <div id="lightbox-title" class="lightbox-title"></div>
        <div class="lightbox-actions">
          <button id="lightbox-toggle" class="lightbox-btn" type="button">原始尺寸</button>
          <button id="lightbox-close" class="lightbox-btn" type="button">关闭</button>
        </div>
      </div>
      <div id="lightbox-stage" class="lightbox-stage fit-width">
        <img id="lightbox-image" alt="">
      </div>
    </div>
  </div>
  <script>
    const state = { sessions: [], filtered: [], activeSessionId: null, activeFile: null, files: [], messages: [] };
    const imageState = { mode: 'fit-width' };

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

    function renderSessionList() {
      const root = document.getElementById('session-list');
      root.innerHTML = state.filtered.map((item) => `
        <article class="session ${item.id === state.activeSessionId ? 'active' : ''}" data-id="${item.id}">
          <h2 class="session-title">${escapeHtml(item.title)}</h2>
          <p class="session-meta">${escapeHtml(item.updated_local)} · ${item.file_count} files</p>
          <p class="session-summary">${escapeHtml(item.id)}</p>
        </article>
      `).join('');
      for (const el of root.querySelectorAll('.session')) {
        el.addEventListener('click', () => selectSession(el.dataset.id));
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
        top.innerHTML = '<h2>Antigravity Sessions</h2><p>选择一个 session 查看内容。</p>';
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
        root.innerHTML = '<div class="empty">选择一个 session。</div>';
        return;
      }

      if (!state.activeFile) {
        root.innerHTML = `
          <div class="conversation-inner">
            ${state.messages.length ? state.messages.map((msg) => `
              <section class="msg">
                <div class="avatar ${msg.role === 'assistant' ? 'assistant' : 'user'}">${msg.role === 'assistant' ? 'AG' : 'ME'}</div>
                <div class="bubble">
                  <div class="bubble-header">
                    <p class="bubble-role">${escapeHtml(msg.role)}</p>
                    <p class="bubble-meta">${escapeHtml(msg.created_at || '')}${msg.step_index !== null && msg.step_index !== undefined ? ` · step ${escapeHtml(String(msg.step_index))}` : ''}</p>
                  </div>
                  <pre class="code-wrap"><code>${escapeHtml(msg.content)}</code></pre>
                </div>
              </section>
            `).join('') : '<div class="empty">这个 session 暂时没有可恢复的聊天记录。</div>'}
          </div>
        `;
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
      toggle.textContent = '原始尺寸';
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
        toggle.textContent = '适应宽度';
      } else {
        imageState.mode = 'fit-width';
        stage.className = 'lightbox-stage fit-width';
        toggle.textContent = '原始尺寸';
      }
    }

    async function selectSession(id) {
      state.activeSessionId = id;
      state.files = await fetchJSON(`/api/sessions/${encodeURIComponent(id)}/files`);
      state.messages = await fetchJSON(`/api/sessions/${encodeURIComponent(id)}/messages`);
      state.activeFile = null;
      renderSessionList();
      renderFileList();
      renderTopbar();
      renderConversation();
      history.replaceState(null, '', `/#${id}`);
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
      state.sessions = await fetchJSON('/api/sessions');
      state.filtered = [...state.sessions];
      document.getElementById('search').addEventListener('input', (e) => applySearch(e.target.value));
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
    def __init__(self, store: Optional[AntigravitySessionStore] = None) -> None:
        self.store = store or AntigravitySessionStore()

    def warm_messages_in_background(self) -> None:
        def _runner() -> None:
            result = self.store.warm_all_messages()
            print("[warm-all]", json.dumps(result, ensure_ascii=False))

        threading.Thread(target=_runner, daemon=True).start()

    def serve(self, host: str = "127.0.0.1", port: int = 8766, open_browser: bool = True) -> None:
        SessionUIHandler.store = self.store
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
