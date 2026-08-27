"""SQLite 数据层：出演者、设置、已见活动快照、通知日志、抓取日志。"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

_DEFAULT_SETTINGS = {
    # 定点模式: 每天这些时刻抓取(本地时区), 优先于间隔模式; 留空则用间隔模式
    "poll_schedule": "01:00,09:00,17:00",
    "poll_interval_hours": 8,  # 间隔模式的间隔(小时), 仅当 poll_schedule 为空时生效
    "fetch_delay_seconds": [3, 8],  # 出演者之间的随机间隔
    "notifiers": {
        "wxpusher": {"enabled": False, "app_token": "", "uid": ""},
        "pushplus": {"enabled": False, "token": ""},
        "email": {
            "enabled": False,
            "host": "",
            "port": 465,
            "username": "",
            "password": "",
            "from_addr": "",
            "to_addr": "",
            "use_ssl": True,
        },
    },
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actors (
    actor_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    baselined INTEGER NOT NULL DEFAULT 0,  -- 0=首次抓取仅建基线,不发通知
    last_fetch_at TEXT,
    last_fetch_ok INTEGER,
    last_error TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS seen_events (
    actor_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    name TEXT,
    date TEXT,
    place TEXT,
    seen_at TEXT DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (actor_id, event_id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    event_id INTEGER,
    channel TEXT,
    title TEXT,
    ok INTEGER NOT NULL,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS fetch_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    actor_name TEXT,
    ok INTEGER NOT NULL,
    events_count INTEGER,
    new_count INTEGER,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events_cache (
    actor_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    name TEXT, date TEXT, place TEXT, open_time TEXT,
    url TEXT,
    PRIMARY KEY (actor_id, event_id)
);
"""


def _merge_settings(base: dict, override: dict) -> dict:
    """深合并: override 优先, 缺失的键从 base(默认值) 补齐。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_settings(out[k], v)
        else:
            out[k] = v
    return out


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)
            row = c.execute("SELECT value FROM settings WHERE key='app'").fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO settings(key, value) VALUES ('app', ?)",
                    (json.dumps(_DEFAULT_SETTINGS, ensure_ascii=False),),
                )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ---------- settings ----------
    def get_settings(self) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key='app'").fetchone()
        stored = json.loads(row["value"]) if row else {}
        # 与默认值合并, 让旧数据库自动获得新增的设置项(如 poll_schedule)
        return _merge_settings(dict(_DEFAULT_SETTINGS), stored)

    def save_settings(self, settings: dict) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES ('app', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(settings, ensure_ascii=False),),
            )

    # ---------- actors ----------
    def list_actors(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM actors ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]

    def add_actor(self, actor_id: int, name: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO actors(actor_id, name) VALUES (?, ?)",
                (actor_id, name),
            )
            return cur.rowcount > 0

    def update_actor(self, actor_id: int, *, enabled: bool | None = None,
                     baselined: bool | None = None, name: str | None = None) -> None:
        sets, args = [], []
        if enabled is not None:
            sets.append("enabled=?"); args.append(int(enabled))
        if baselined is not None:
            sets.append("baselined=?"); args.append(int(baselined))
        if name is not None:
            sets.append("name=?"); args.append(name)
        if not sets:
            return
        args.append(actor_id)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE actors SET {', '.join(sets)} WHERE actor_id=?", args)

    def delete_actor(self, actor_id: int) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM actors WHERE actor_id=?", (actor_id,))
            c.execute("DELETE FROM seen_events WHERE actor_id=?", (actor_id,))
            c.execute("DELETE FROM events_cache WHERE actor_id=?", (actor_id,))

    def record_fetch(self, actor_id: int, ok: bool, error: str | None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE actors SET last_fetch_at=datetime('now','localtime'), "
                "last_fetch_ok=?, last_error=? WHERE actor_id=?",
                (int(ok), error, actor_id),
            )

    # ---------- events / dedup ----------
    def get_seen_ids(self, actor_id: int) -> set[int]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_id FROM seen_events WHERE actor_id=?", (actor_id,)
            ).fetchall()
            return {r["event_id"] for r in rows}

    def mark_seen(self, actor_id: int, events: list) -> None:
        with self._lock, self._conn() as c:
            for ev in events:
                c.execute(
                    "INSERT OR IGNORE INTO seen_events(actor_id, event_id, name, date, place) "
                    "VALUES (?,?,?,?,?)",
                    (actor_id, ev.event_id, ev.name, ev.date, ev.place),
                )
                c.execute(
                    "INSERT OR REPLACE INTO events_cache"
                    "(actor_id, event_id, name, date, place, open_time, url) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (actor_id, ev.event_id, ev.name, ev.date, ev.place, ev.open_time, ev.url()),
                )

    def list_events(self, actor_id: int | None = None, future_only: bool = False) -> list[dict]:
        q = (
            "SELECT e.*, a.name AS actor_name FROM events_cache e "
            "JOIN actors a ON a.actor_id = e.actor_id"
        )
        conds, args = [], []
        if actor_id is not None:
            conds.append("e.actor_id=?"); args.append(actor_id)
        if future_only:
            conds.append("e.date >= date('now','localtime')")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY e.date, e.event_id"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    # ---------- 配置导出/导入 ----------
    def export_snapshot(self) -> list[dict]:
        """导出活动快照(seen_events + events_cache + 出演者基线状态)。"""
        with self._conn() as c:
            seen = [
                dict(r) for r in c.execute(
                    "SELECT actor_id, event_id, name, date, place FROM seen_events"
                ).fetchall()
            ]
            cache = [
                dict(r) for r in c.execute(
                    "SELECT actor_id, event_id, name, date, place, open_time, url FROM events_cache"
                ).fetchall()
            ]
        return {"seen_events": seen, "events_cache": cache}

    def import_snapshot(self, snapshot: dict) -> None:
        """恢复活动快照。会先清掉该出演者已有的快照, 以导入数据为准。"""
        with self._lock, self._conn() as c:
            for row in snapshot.get("seen_events", []):
                c.execute(
                    "INSERT OR REPLACE INTO seen_events(actor_id, event_id, name, date, place) "
                    "VALUES (?,?,?,?,?)",
                    (row["actor_id"], row["event_id"], row.get("name"),
                     row.get("date"), row.get("place")),
                )
            for row in snapshot.get("events_cache", []):
                c.execute(
                    "INSERT OR REPLACE INTO events_cache"
                    "(actor_id, event_id, name, date, place, open_time, url) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (row["actor_id"], row["event_id"], row.get("name"), row.get("date"),
                     row.get("place"), row.get("open_time"), row.get("url")),
                )

    def set_baselined(self, actor_id: int, baselined: bool) -> None:
        self.update_actor(actor_id, baselined=baselined)

    # ---------- notifications ----------
    def log_notification(self, actor_id: int | None, event_id: int | None,
                         channel: str, title: str, ok: bool, error: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO notifications(actor_id, event_id, channel, title, ok, error) "
                "VALUES (?,?,?,?,?,?)",
                (actor_id, event_id, channel, title, int(ok), error),
            )

    def list_notifications(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- 抓取日志 ----------
    def log_fetch(self, actor_id: int, actor_name: str, ok: bool,
                  events_count: int = 0, new_count: int = 0,
                  error: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO fetch_logs(actor_id, actor_name, ok, events_count, new_count, error) "
                "VALUES (?,?,?,?,?,?)",
                (actor_id, actor_name, int(ok), events_count, new_count, error),
            )

    def list_fetch_logs(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM fetch_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
