"""SQLite 持久化：任务与资源状态。

单连接 + 互斥锁：下载线程和 HTTP 线程共用，WAL 模式降低写冲突。
"""

from __future__ import annotations

import sqlite3
import threading
import json
import time
import uuid
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    url_key       TEXT NOT NULL,
    status        TEXT NOT NULL,
    title         TEXT,
    author        TEXT,
    author_id     TEXT,
    aweme_id      TEXT,
    platform      TEXT NOT NULL DEFAULT 'douyin',
    out_dir       TEXT,
    out_base      TEXT,
    payload       TEXT,
    quality       TEXT,
    stage         TEXT,
    total_assets  INTEGER NOT NULL DEFAULT 0,
    done_assets   INTEGER NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    bytes_done    INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    job_id      TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    params      TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    progress    REAL NOT NULL DEFAULT 0,
    output_path TEXT,
    error       TEXT,
    PRIMARY KEY (job_id, idx)
);

CREATE TABLE IF NOT EXISTS assets (
    job_id   TEXT NOT NULL,
    idx      INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    url      TEXT NOT NULL,
    path     TEXT,
    size     INTEGER NOT NULL DEFAULT 0,
    status   TEXT NOT NULL DEFAULT 'pending',
    error    TEXT,
    PRIMARY KEY (job_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs(url_key, status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS music_jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL DEFAULT 'song',
    provider      TEXT,
    song_id       TEXT,
    dedupe_key    TEXT,
    title         TEXT,
    artist        TEXT,
    album         TEXT,
    status        TEXT NOT NULL,
    stage         TEXT,
    progress      REAL NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    bytes_done    INTEGER NOT NULL DEFAULT 0,
    target        TEXT,
    src_path      TEXT,
    error         TEXT,
    payload       TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_music_dedupe ON music_jobs(dedupe_key, status);
CREATE INDEX IF NOT EXISTS idx_music_created ON music_jobs(created_at DESC);
"""


class Store:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """老库补列，避免升级后 500。"""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for column, ddl in (
            ("out_base", "TEXT"),
            ("payload", "TEXT"),
            ("platform", "TEXT NOT NULL DEFAULT 'douyin'"),
            ("author_id", "TEXT"),
            ("quality", "TEXT"),
            ("stage", "TEXT"),
        ):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")

    # ---------- 任务 ----------

    def create_job(self, url: str, url_key: str, payload: str | None = None,
                   out_base: str | None = None, platform: str = "douyin") -> str:
        jid = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, url, url_key, status, payload, out_base,"
                " platform, created_at, updated_at)"
                " VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                (jid, url, url_key, payload, out_base, platform, now, now),
            )
            self._conn.commit()
        return jid

    def update_job(self, jid: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status", "title", "author", "author_id", "aweme_id", "platform",
            "out_dir", "out_base", "payload", "total_assets", "done_assets",
            "bytes_total", "bytes_done", "error", "quality", "stage",
        }
        sets = [(k, v) for k, v in fields.items() if k in allowed]
        if not sets:
            return
        clause = ", ".join(f"{k} = ?" for k, _ in sets)
        values = [v for _, v in sets] + [time.time(), jid]
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {clause}, updated_at = ? WHERE id = ?", values
            )
            self._conn.commit()

    def get_job(self, jid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def find_done_by_key(self, url_key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE url_key = ? AND status = 'done'"
                " ORDER BY created_at DESC LIMIT 1",
                (url_key,),
            ).fetchone()
        return dict(row) if row else None

    def find_active_by_key(self, url_key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE url_key = ?"
                " AND status IN ('queued','running','paused')"
                " ORDER BY created_at DESC LIMIT 1",
                (url_key,),
            ).fetchone()
        return dict(row) if row else None

    def clear_finished(self) -> int:
        """删掉已完成/失败/已取消的视频任务（资源与步骤一起清）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status IN ('done','failed','cancelled')"
            ).fetchall()
            ids = [row["id"] for row in rows]
            for jid in ids:
                self._conn.execute("DELETE FROM assets WHERE job_id = ?", (jid,))
                self._conn.execute("DELETE FROM steps WHERE job_id = ?", (jid,))
                self._conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
            self._conn.commit()
        return len(ids)

    def recover_interrupted(self) -> list[str]:
        """启动时把上次没跑完的任务放回队列（而不是标成失败）。

        直接标失败的话，关一次窗口就得手点几十次「重试」；排队/下载中的任务
        本来就没出错，重启后接着跑才是对的。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status IN ('queued', 'running')"
                " ORDER BY created_at"
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                self._conn.execute(
                    "UPDATE jobs SET status = 'queued', error = NULL,"
                    " stage = NULL, updated_at = ? WHERE status IN ('queued', 'running')",
                    (time.time(),),
                )
                self._conn.commit()
        return ids

    # ---------- 资源 ----------

    def add_assets(self, jid: str, assets: Iterable[dict]) -> None:
        rows = [
            (jid, a["idx"], a["kind"], a["url"], None, 0, "pending", None)
            for a in assets
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO assets (job_id, idx, kind, url, path, size,"
                " status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.execute(
                "UPDATE jobs SET total_assets = ?, updated_at = ? WHERE id = ?",
                (len(rows), time.time(), jid),
            )
            self._conn.commit()

    def update_asset(self, jid: str, idx: int, **fields: Any) -> None:
        allowed = {"path", "size", "status", "error", "url"}
        sets = [(k, v) for k, v in fields.items() if k in allowed]
        if not sets:
            return
        clause = ", ".join(f"{k} = ?" for k, _ in sets)
        values = [v for _, v in sets] + [jid, idx]
        with self._lock:
            self._conn.execute(
                f"UPDATE assets SET {clause} WHERE job_id = ? AND idx = ?", values
            )
            self._conn.commit()

    def clear_assets(self, jid: str) -> None:
        """降级重试时清掉上一轮的资源行，避免残留音频行卡住进度。"""
        with self._lock:
            self._conn.execute("DELETE FROM assets WHERE job_id = ?", (jid,))
            self._conn.execute(
                "UPDATE jobs SET total_assets = 0, done_assets = 0,"
                " updated_at = ? WHERE id = ?",
                (time.time(), jid),
            )
            self._conn.commit()

    # ---------- 步骤 ----------

    def add_step(self, jid: str, idx: int, kind: str,
                 params: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO steps (job_id, idx, kind, params, status,"
                " progress) VALUES (?, ?, ?, ?, 'pending', 0)",
                (jid, idx, kind, json.dumps(params or {}, ensure_ascii=False)),
            )
            self._conn.commit()

    def update_step(self, jid: str, idx: int, **fields: Any) -> None:
        allowed = {"status", "progress", "output_path", "error"}
        sets = [(k, v) for k, v in fields.items() if k in allowed]
        if not sets:
            return
        clause = ", ".join(f"{k} = ?" for k, _ in sets)
        values = [v for _, v in sets] + [jid, idx]
        with self._lock:
            self._conn.execute(
                f"UPDATE steps SET {clause} WHERE job_id = ? AND idx = ?", values
            )
            self._conn.commit()

    def list_steps(self, jid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM steps WHERE job_id = ? ORDER BY idx", (jid,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_assets(self, jid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM assets WHERE job_id = ? ORDER BY idx", (jid,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 音乐任务 ----------

    def create_music_job(self, *, payload: str, dedupe_key: str, provider: str,
                         song_id: str, title: str, artist: str, album: str,
                         target: str, kind: str = "song") -> str:
        jid = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO music_jobs (id, kind, provider, song_id, dedupe_key,"
                " title, artist, album, status, target, payload, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                (jid, kind, provider, song_id, dedupe_key, title, artist, album,
                 target, payload, now, now),
            )
            self._conn.commit()
        return jid

    def update_music_job(self, jid: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "kind", "provider", "song_id", "title", "artist", "album", "status",
            "stage", "progress", "bytes_total", "bytes_done", "target",
            "src_path", "error", "payload",
        }
        sets = [(key, value) for key, value in fields.items() if key in allowed]
        if not sets:
            return
        clause = ", ".join(f"{key} = ?" for key, _ in sets)
        values = [value for _, value in sets] + [time.time(), jid]
        with self._lock:
            self._conn.execute(
                f"UPDATE music_jobs SET {clause}, updated_at = ? WHERE id = ?",
                values,
            )
            self._conn.commit()

    def get_music_job(self, jid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM music_jobs WHERE id = ?", (jid,)
            ).fetchone()
        return dict(row) if row else None

    def list_music_jobs(self, limit: int = 300) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM music_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_music_job(self, dedupe_key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM music_jobs WHERE dedupe_key = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (dedupe_key,),
            ).fetchone()
        return dict(row) if row else None

    def recover_music(self) -> list[str]:
        """重启后把没跑完的音乐任务放回队列。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM music_jobs WHERE status IN ('queued', 'running')"
                " ORDER BY created_at"
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                self._conn.execute(
                    "UPDATE music_jobs SET status = 'queued', stage = NULL,"
                    " error = NULL, updated_at = ? WHERE status IN ('queued', 'running')",
                    (time.time(),),
                )
                self._conn.commit()
        return ids

    def clear_music_finished(self) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM music_jobs WHERE status IN ('done', 'failed', 'cancelled')"
            )
            self._conn.commit()
            return int(cursor.rowcount or 0)

    def music_counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT"
                "  SUM(status = 'queued')   AS queued,"
                "  SUM(status = 'running')  AS running,"
                "  SUM(status = 'paused')   AS paused,"
                "  SUM(status = 'done')     AS done,"
                "  SUM(status = 'failed')   AS failed,"
                "  SUM(status = 'cancelled') AS cancelled"
                " FROM music_jobs"
            ).fetchone()
        return {
            key: int(row[key] or 0)
            for key in ("queued", "running", "paused", "done", "failed", "cancelled")
        }

    def counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT"
                "  SUM(status = 'queued')   AS queued,"
                "  SUM(status = 'running')  AS running,"
                "  SUM(status = 'done')     AS done,"
                "  SUM(status = 'failed')   AS failed,"
                "  SUM(status = 'paused')   AS paused"
                " FROM jobs"
            ).fetchone()
        return {
            k: int(row[k] or 0)
            for k in ("queued", "running", "done", "failed", "paused")
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
