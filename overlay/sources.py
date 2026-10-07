"""Where the desktop pet learns what Claude Code and Codex are doing.

Both sources produce `Activity` rows: one per conversation, with a status in
the four the Codex pet uses (running, waiting, blocked, ready) or idle.

- Claude Code: one file per session that the codex-pet mod keeps current,
  ~/.codex-pet/sessions/cc-<cli session id>.json, plus the desktop app's own
  session records for the real title and the id its deep link takes.
- Codex: its thread index (~/.codex/session_index.jsonl) for titles and
  recency, and each recent thread's rollout for what it is doing.
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SHARED = Path.home() / ".codex-pet"
CC_DIR = SHARED / "sessions"
CLAUDE_SESSIONS = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Claude" / "claude-code-sessions"

CC_GONE_S = 150  # the mod heartbeats every 30 s
CODEX_RECENT_S = 6 * 3600
CODEX_STALE_RUN_S = 15 * 60
READY_REPLAY_S = 10 * 60  # a turn that finished before the pet started counts if this recent
READY_EXPIRE_S = 3 * 3600


@dataclass
class Activity:
    key: str  # "cc:<id>" / "codex:<id>"
    agent: str  # "cc" | "codex"
    title: str
    status: str  # running | waiting | blocked | ready | idle
    action: str = ""
    status_at: float = 0.0  # epoch seconds the status began
    url: str = ""  # what opening the card launches
    pet_id: str = ""
    extra: dict = field(default_factory=dict)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class ClaudeCodeSource:
    def __init__(self):
        self.desktop = {}  # cli session id -> (local id, title)
        self.desktop_scan = 0.0

    def _desktop_records(self, wanted):
        """The desktop app's record of each Code-tab session: its title and local id."""
        if not CLAUDE_SESSIONS.is_dir():
            return
        missing = [w for w in wanted if w not in self.desktop]
        if not missing and time.time() - self.desktop_scan < 30:
            return
        if time.time() - self.desktop_scan < 5:
            return
        self.desktop_scan = time.time()
        for record in CLAUDE_SESSIONS.glob("*/*/local_*.json"):
            try:
                if time.time() - record.stat().st_mtime > 2 * 86400:
                    continue  # a session in use rewrites its record
            except OSError:
                continue
            data = _read_json(record) or {}
            cli = data.get("cliSessionId")
            if cli:
                self.desktop[cli] = (data.get("sessionId", record.stem), data.get("title") or "")

    def poll(self):
        rows = []
        if not CC_DIR.is_dir():
            return rows
        now = time.time()
        files = list(CC_DIR.glob("cc-*.json"))
        states = []
        for f in files:
            data = _read_json(f)
            if not data:
                continue
            gone = now - data.get("updatedAt", 0) / 1000 > CC_GONE_S
            if data.get("status") == "ended" or gone:
                try:
                    f.unlink()
                except OSError:
                    pass
                continue
            states.append(data)
        self._desktop_records([s.get("id") for s in states])
        for s in states:
            local, title = self.desktop.get(s.get("id"), (None, ""))
            title = title or s.get("firstPrompt") or Path(s.get("cwd") or "Claude Code").name
            url = f"claude://code/continue?session={local}" if local else "claude://code/continue?session=last"
            rows.append(Activity(
                key=f"cc:{s.get('id')}", agent="cc", title=title, status=s.get("status", "idle"),
                action=s.get("action") or "", status_at=s.get("statusAt", 0) / 1000, url=url,
                pet_id=s.get("petId") or "", extra={"updatedAt": s.get("updatedAt", 0)},
            ))
        return rows


class _Thread:
    def __init__(self, path: Path):
        self.path = path
        self.offset = max(0, path.stat().st_size - 512 * 1024)
        self.partial = b""
        self.status = "idle"
        self.status_at = 0.0
        self.action = ""
        self.cwd = ""
        self.is_subagent = False
        self.replaying = True


def _describe(item: dict) -> str:
    """What a completed Codex item says the thread is doing, in a few words."""
    kind = item.get("type")
    if kind == "CommandExecution":
        parsed = item.get("parsed_cmd") or []
        first = parsed[0].get("type") if parsed and isinstance(parsed[0], dict) else ""
        if item.get("exit_code") not in (None, 0):
            return "指令失敗"
        return {"read": "讀檔案", "search": "搜尋檔案", "list_files": "列出檔案"}.get(first, "執行了指令")
    if kind == "FileChange":
        changes = item.get("changes") or {}
        n = len(changes) if isinstance(changes, (dict, list)) else 1
        return f"編輯了 {n} 個檔案"
    if kind in ("McpToolCall", "DynamicToolCall"):
        return f"呼叫 {item.get('tool') or '工具'}"
    if kind == "Extension" and item.get("kind") == "web.search":
        q = (item.get("query") or "").strip()
        return f"搜尋「{q[:24]}」" if q else "搜尋網頁"
    if kind == "SubAgentActivity" and item.get("kind") == "started":
        return "派出子代理"
    if kind == "AgentMessage":
        for part in item.get("content") or []:
            text = (part.get("text") or "").strip() if isinstance(part, dict) else ""
            if text:
                return text.splitlines()[0][:60]
    if kind == "Reasoning":
        return "思考中"
    if kind == "ContextCompaction":
        return "整理對話"
    return ""


class CodexSource:
    def __init__(self, home: Path):
        self.home = home
        self.index = {}  # thread id -> (name, updated epoch)
        self.index_mtime = 0.0
        self.threads = {}  # id -> _Thread
        self.paths = {}  # id -> rollout path (or None while unknown)
        self.path_scan = {}

    def _read_index(self):
        path = self.home / "session_index.jsonl"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == self.index_mtime:
            return
        self.index_mtime = mtime
        index = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    stamp = _epoch(row.get("updated_at"))
                    index[row.get("id")] = (row.get("thread_name") or "", stamp)
        except OSError:
            return
        self.index = index

    def _find(self, tid: str):
        if tid in self.paths and self.paths[tid]:
            return self.paths[tid]
        if time.time() - self.path_scan.get(tid, 0) < 30:
            return None
        self.path_scan[tid] = time.time()
        found = next((self.home / "sessions").glob(f"*/*/*/rollout-*{tid}.jsonl"), None)
        self.paths[tid] = found
        return found

    def poll(self):
        self._read_index()
        now = time.time()
        rows = []
        for tid, (name, updated) in self.index.items():
            if not tid or now - updated > CODEX_RECENT_S:
                continue
            thread = self.threads.get(tid)
            if thread is None:
                path = self._find(tid)
                if path is None:
                    continue
                try:
                    thread = self.threads[tid] = _Thread(path)
                except OSError:
                    continue
            self._follow(thread)
            if thread.is_subagent:
                continue
            status, at = thread.status, thread.status_at
            if status == "running":
                try:
                    if now - thread.path.stat().st_mtime > CODEX_STALE_RUN_S:
                        status = "idle"
                except OSError:
                    status = "idle"
            if status == "ready" and now - at > READY_EXPIRE_S:
                status = "idle"
            rows.append(Activity(
                key=f"codex:{tid}", agent="codex", title=name or Path(thread.cwd or "Codex").name,
                status=status, action=thread.action, status_at=at, url=f"codex://threads/{tid}",
            ))
        return rows

    def _follow(self, t: _Thread):
        try:
            with open(t.path, "rb") as fh:
                if t.offset == 0 or t.replaying:
                    head = fh.readline()
                    self._meta(t, head)
                fh.seek(t.offset)
                chunk = fh.read()
        except OSError:
            return
        replay = t.replaying
        t.replaying = False
        if not chunk:
            return
        t.offset += len(chunk)
        lines = (t.partial + chunk).split(b"\n")
        t.partial = lines.pop()
        for raw in lines:
            if b'"event_msg"' not in raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            p = row.get("payload") or {}
            kind = p.get("type")
            at = _epoch(row.get("timestamp")) or time.time()
            if kind == "task_started":
                t.status, t.status_at, t.action = "running", at, ""
            elif kind == "item_completed":
                said = _describe(p.get("item") or {})
                if said:
                    t.action = said
            elif kind == "task_complete":
                done = _epoch_s(p.get("completed_at")) or at
                fresh = not replay or time.time() - done < READY_REPLAY_S
                t.status, t.status_at = ("ready" if fresh else "idle"), done
                summary = (p.get("last_agent_message") or "").strip()
                if summary:
                    t.action = summary.splitlines()[0][:60]
            elif kind == "turn_aborted":
                t.status, t.status_at, t.action = "idle", at, ""
            elif kind == "error":
                t.status, t.status_at = "blocked", at
                t.action = str(p.get("message") or "發生錯誤")[:60]

    def _meta(self, t: _Thread, head: bytes):
        try:
            p = json.loads(head).get("payload") or {}
        except ValueError:
            return
        t.cwd = p.get("cwd") or ""
        t.is_subagent = p.get("thread_source") == "subagent"


def _epoch(stamp) -> float:
    if not stamp:
        return 0.0
    text = str(stamp).replace("Z", "+00:00")
    # Codex writes 7 fractional digits; fromisoformat takes at most 6.
    head, dot, rest = text.partition(".")
    if dot:
        n = len(rest) - len(rest.lstrip("0123456789"))
        text = f"{head}.{rest[:min(n, 6)]}{rest[n:]}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _epoch_s(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
