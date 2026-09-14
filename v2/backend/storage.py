"""M5 持久化：行程与体检事件的 SQLite 存储。

为什么需要它：
- M1 遗留问题「记忆只在内存，重启丢上下文」——行程落库后，重启也能继续体检/追问
- M5 的「行程体检」需要一个可寻址的行程对象（按 thread_id 取回最近一次行程）

设计取舍：
- 只用标准库 `sqlite3`，不引 ORM：表只有两张，SQL 直白可读，部署零成本
- WAL 模式 + 单连接 + 线程锁：写少读多，本机单进程场景足够
- 写操作都是毫秒级，直接在事件循环里调用（不额外开线程池）
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    thread_id   TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    plan_json   TEXT NOT NULL,
    answer      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trip_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_thread ON trip_events(thread_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def init_db(db_path: str | Path) -> Path:
    """初始化数据库文件与表结构（幂等）。返回实际使用的路径。"""
    global _CONN, _DB_PATH
    path = Path(db_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _CONN = sqlite3.connect(str(path), check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.executescript("PRAGMA journal_mode=WAL;\n" + SCHEMA)
        _CONN.commit()
        _DB_PATH = path
    return path


def _conn() -> sqlite3.Connection:
    if _CONN is None:
        raise RuntimeError("存储未初始化：请先调用 init_db()")
    return _CONN


def close_db() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
            _CONN = None


def save_trip(
    thread_id: str,
    plan: dict[str, Any],
    answer: str = "",
    title: str = "",
) -> None:
    """写入/覆盖一条行程（同一 thread_id 只保留最近一次）。"""
    now = _now()
    payload = json.dumps(plan, ensure_ascii=False)
    title = title or str(plan.get("title") or "")
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO trips (thread_id, title, plan_json, answer, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                title=excluded.title,
                plan_json=excluded.plan_json,
                answer=excluded.answer,
                updated_at=excluded.updated_at
            """,
            (thread_id, title, payload, answer, now, now),
        )
        conn.commit()


def get_trip(thread_id: str) -> dict[str, Any] | None:
    """按 thread_id 取回行程；不存在返回 None。"""
    with _LOCK:
        row = _conn().execute(
            "SELECT thread_id, title, plan_json, answer, updated_at FROM trips WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "thread_id": row["thread_id"],
        "title": row["title"],
        "plan": json.loads(row["plan_json"]),
        "answer": row["answer"],
        "updated_at": row["updated_at"],
    }


def list_trips(limit: int = 20) -> list[dict[str, Any]]:
    """最近更新的行程列表（不含完整 plan，给「历史行程」入口用）。"""
    with _LOCK:
        rows = _conn().execute(
            "SELECT thread_id, title, updated_at FROM trips ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_event(thread_id: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    """记录一条体检/变更事件，用于「行程变化史」。"""
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO trip_events (thread_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, kind, json.dumps(payload or {}, ensure_ascii=False), _now()),
        )
        conn.commit()


def list_events(thread_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT id, kind, payload, created_at FROM trip_events "
            "WHERE thread_id=? ORDER BY id DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "payload": json.loads(r["payload"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


__all__ = [
    "init_db",
    "close_db",
    "save_trip",
    "get_trip",
    "list_trips",
    "add_event",
    "list_events",
]
