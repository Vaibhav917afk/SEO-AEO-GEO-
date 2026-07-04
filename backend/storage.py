import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunStore:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    ok INTEGER NOT NULL
                )
                """
            )

    def create_run(self, prompt: str, plan: dict[str, Any], result: dict[str, Any], ok: bool) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (created_at, prompt, plan_json, result_json, ok) VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    prompt,
                    json.dumps(plan, ensure_ascii=True),
                    json.dumps(result, ensure_ascii=True),
                    1 if ok else 0,
                ),
            )
            return int(cursor.lastrowid)

    def recent_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, prompt, plan_json, result_json, ok FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "prompt": row[2],
                "plan": json.loads(row[3]),
                "result": json.loads(row[4]),
                "ok": bool(row[5]),
            }
            for row in rows
        ]
