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
        """)
        self.db.commit()

    def conversation(self, text: str) -> str:
        identifier = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO conversations VALUES (?, ?, ?)",
                            (identifier, text[:70], time.time()))
        return identifier

    def list_conversations(self) -> list[dict]:
        with self.lock:
            return [dict(zip(("id", "title", "updated"), r)) for r in self.db.execute(
                "SELECT * FROM conversations ORDER BY updated DESC LIMIT 100")]

    def messages(self, conversation: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT role, content FROM messages WHERE conversation=? "
                                   "ORDER BY id", (conversation,)).fetchall()
        return [dict(role=role, content=content) for role, content in rows]

    def add_message(self, conversation: str, role: str, content: str):
        with self.lock, self.db:
            self.db.execute("INSERT INTO messages(conversation, role, content) VALUES (?, ?, ?)",
                            (conversation, role, content))
            self.db.execute("UPDATE conversations SET updated=? WHERE id=?",
                            (time.time(), conversation))

    def save_task(self, state: dict):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)",
                            (state["id"], state["conversation"], json.dumps(state)))

    def tasks(self) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT state FROM tasks ORDER BY rowid")]

    def begin_call(self, identifier: str, task: str, name: str, arguments: dict):
        with self.lock, self.db:
            previous = self.db.execute("SELECT status, result FROM calls WHERE id=?", (identifier,)).fetchone()
            if previous:
                if previous[0] == "done":
                    return json.loads(previous[1])
                raise RuntimeError("An earlier action has an uncertain outcome. Inspect it before starting a new task.")
            self.db.execute("INSERT INTO calls VALUES (?, ?, ?, ?, 'started', NULL)",
                            (identifier, task, name, json.dumps(arguments)))
        return None

    def finish_call(self, identifier: str, result: dict):
        with self.lock, self.db:
            self.db.execute("UPDATE calls SET status='done', result=? WHERE id=?",
                            (json.dumps(result), identifier))

    def delete(self, conversation: str = ""):
        with self.lock, self.db:
            if conversation:
                self.db.execute("DELETE FROM calls WHERE task IN (SELECT id FROM tasks WHERE conversation=?)", (conversation,))
                for table in ("tasks", "messages"):
                    self.db.execute(f"DELETE FROM {table} WHERE conversation=?", (conversation,))
                self.db.execute("DELETE FROM conversations WHERE id=?", (conversation,))
            else:
                for table in ("calls", "tasks", "messages", "conversations"):
                    self.db.execute(f"DELETE FROM {table}")
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self):
        self.db.close()
