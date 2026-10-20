"""Persistent event-id uniqueness tracking."""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def init_db(path: str) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path), check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_event_ids (
            event_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def record_if_new(
    connection: sqlite3.Connection, lock: threading.Lock, event_id: str
) -> bool:
    try:
        with lock:
            connection.execute(
                "INSERT INTO seen_event_ids (event_id, created_at) VALUES (?, ?)",
                (event_id, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
            return True
    except sqlite3.IntegrityError:
        return False
