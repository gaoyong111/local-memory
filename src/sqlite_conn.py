"""SQLite 连接统一入口 — WAL + busy_timeout，HTTP 模式下多设备并发写防护。"""

from __future__ import annotations

import sqlite3


def connect_db(path, *, timeout: float = 10.0) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=10000')
    return conn
