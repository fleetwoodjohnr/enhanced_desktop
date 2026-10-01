"""Local history and a durable journal around every tool invocation."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class History:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / "history.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, conversation TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, conversation TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, task TEXT NOT NULL, name TEXT NOT NULL,
                arguments TEXT NOT NULL, status TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS usage (
                integration TEXT PRIMARY KEY, last_used REAL NOT NULL, last_action TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY, conversation TEXT NOT NULL, message INTEGER NOT NULL,
                name TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, label TEXT NOT NULL,
                text TEXT);
        """)
        # CREATE TABLE IF NOT EXISTS never adds columns to an existing table,
        # so journals from before App Access gain them here.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(calls)")}
        if "integration" not in columns:
            self.db.execute("ALTER TABLE calls ADD COLUMN integration TEXT NOT NULL DEFAULT ''")
        if "started" not in columns:
            self.db.execute("ALTER TABLE calls ADD COLUMN started REAL")
        self.db.commit()

    def conversation(self, text: str) -> str:
        identifier = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO conversations VALUES (?, ?, ?)",
                            (identifier, text[:70], time.time()))
        return identifier

    def list_conversations(self, limit: int | None = 100) -> list[dict]:
        query = "SELECT * FROM conversations ORDER BY updated DESC"
        with self.lock:
            rows = self.db.execute(query + (f" LIMIT {int(limit)}" if limit else ""))
            return [dict(zip(("id", "title", "updated"), r)) for r in rows]

    def messages(self, conversation: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, role, content FROM messages WHERE conversation=? "
                                   "ORDER BY id", (conversation,)).fetchall()
        files = self.message_attachments(conversation) if rows else {}
        result = []
        for identifier, role, content in rows:
            message = {"role": role, "content": content}
            if identifier in files:
                message["attachments"] = files[identifier]
            result.append(message)
        return result

    def add_message(self, conversation: str, role: str, content: str) -> int:
        with self.lock, self.db:
            cursor = self.db.execute("INSERT INTO messages(conversation, role, content) VALUES (?, ?, ?)",
                                     (conversation, role, content))
            self.db.execute("UPDATE conversations SET updated=? WHERE id=?",
                            (time.time(), conversation))
            return cursor.lastrowid

    def add_attachments(self, conversation: str, message: int, items: list[dict]):
        """Keep what was attached, so later questions in the chat still have it.

        Text is kept as extracted -- the file may move or change -- and images
        by path only; an image is large, and it is re-read if still there.
        """
        with self.lock, self.db:
            self.db.executemany(
                "INSERT INTO attachments(conversation, message, name, kind, path, label, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(conversation, message, item["name"], item["kind"], item["path"], item.get("label", ""),
                  item.get("text") if item["kind"] == "text" else None) for item in items])

    def attachments(self, conversation: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT message, name, kind, path, label, text FROM attachments "
                                   "WHERE conversation=? ORDER BY id", (conversation,)).fetchall()
        return [{"message": r[0], "name": r[1], "kind": r[2], "path": r[3], "label": r[4],
                 "text": r[5] or "", "truncated": False} for r in rows]

    def message_attachments(self, conversation: str) -> dict[int, list[dict]]:
        found: dict[int, list[dict]] = {}
        for item in self.attachments(conversation):
            found.setdefault(item["message"], []).append(
                {"name": item["name"], "kind": item["kind"], "label": item["label"]})
        return found

    def save_task(self, state: dict):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)",
                            (state["id"], state["conversation"], json.dumps(state)))

    def tasks(self) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT state FROM tasks ORDER BY rowid")]

    def begin_call(self, identifier: str, task: str, name: str, arguments: dict,
                   integration: str = ""):
        with self.lock, self.db:
            previous = self.db.execute("SELECT status, result FROM calls WHERE id=?", (identifier,)).fetchone()
            if previous:
                if previous[0] == "done":
                    return json.loads(previous[1])
                raise RuntimeError("An earlier action has an uncertain outcome. Inspect it before starting a new task.")
            self.db.execute("INSERT INTO calls(id, task, name, arguments, status, result, integration, started) "
                            "VALUES (?, ?, ?, ?, 'started', NULL, ?, ?)",
                            (identifier, task, name, json.dumps(arguments), integration, time.time()))
        return None

    def call_result(self, identifier: str):
        with self.lock:
            row = self.db.execute("SELECT name, arguments, status, result FROM calls WHERE id=?",
                                  (identifier,)).fetchone()
        if not row:
            return None
        return {"name": row[0], "arguments": json.loads(row[1]), "status": row[2],
                "result": json.loads(row[3]) if row[3] else None}

    def record_usage(self, integration: str, action: str):
        """When CLIVE last used an app, for App Access. Kept apart from chats."""
        if not integration:
            return
        with self.lock, self.db:
            self.db.execute("INSERT INTO usage VALUES (?, ?, ?) ON CONFLICT(integration) DO UPDATE "
                            "SET last_used=excluded.last_used, last_action=excluded.last_action",
                            (integration, time.time(), action[:200]))

    def usage(self) -> dict:
        with self.lock:
            return {row[0]: {"time": row[1], "action": row[2]}
                    for row in self.db.execute("SELECT integration, last_used, last_action FROM usage")}

    def clear_usage(self):
        with self.lock, self.db:
            self.db.execute("DELETE FROM usage")

    def finish_call(self, identifier: str, result: dict):
        with self.lock, self.db:
            self.db.execute("UPDATE calls SET status='done', result=? WHERE id=?",
                            (json.dumps(result), identifier))

    def delete(self, conversation: str = ""):
        with self.lock, self.db:
            if conversation:
                self.db.execute("DELETE FROM calls WHERE task IN (SELECT id FROM tasks WHERE conversation=?)", (conversation,))
                for table in ("tasks", "messages", "attachments"):
                    self.db.execute(f"DELETE FROM {table} WHERE conversation=?", (conversation,))
                self.db.execute("DELETE FROM conversations WHERE id=?", (conversation,))
            else:
                for table in ("calls", "tasks", "messages", "attachments", "conversations"):
                    self.db.execute(f"DELETE FROM {table}")
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self):
        self.db.close()
