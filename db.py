"""
SQLite-backed storage for warnings, timeout escalation, invite offense
counts, and staff-configured exempt invite links.

Uses a single connection guarded by a lock — plenty for a single-server bot.
"""

from __future__ import annotations

import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    reason TEXT,
    serious INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings(guild_id, user_id);

CREATE TABLE IF NOT EXISTS timeout_levels (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    level INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS invite_offenses (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS exempt_invites (
    guild_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    added_by INTEGER,
    added_at REAL NOT NULL,
    PRIMARY KEY (guild_id, code)
);
"""


class Database:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # RLock: some public methods call other public methods internally
        # (e.g. bump_invite_offense -> invite_offense_count)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Warnings
    # ------------------------------------------------------------------
    def add_warning(self, guild_id: int, user_id: int, category: str,
                    reason: str, serious: bool,
                    expires_at: float | None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO warnings (guild_id, user_id, category, reason,"
                " serious, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                (guild_id, user_id, category, reason, int(serious),
                 time.time(), expires_at),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def active_warnings(self, guild_id: int, user_id: int,
                        now: float | None = None) -> list[sqlite3.Row]:
        """Non-serious warnings that have not yet expired (the 24h cycle)."""
        now = time.time() if now is None else now
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM warnings WHERE guild_id=? AND user_id=?"
                " AND serious=0 AND (expires_at IS NULL OR expires_at>?)"
                " ORDER BY created_at ASC",
                (guild_id, user_id, now),
            ).fetchall()
        return list(rows)

    def active_warning_count(self, guild_id: int, user_id: int,
                             now: float | None = None) -> int:
        return len(self.active_warnings(guild_id, user_id, now))

    def clear_small_warnings(self, guild_id: int, user_id: int) -> int:
        """Delete all non-serious warnings (after a timeout is served)."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM warnings WHERE guild_id=? AND user_id=?"
                " AND serious=0",
                (guild_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount

    def prune_expired(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM warnings WHERE expires_at IS NOT NULL"
                " AND expires_at < ?",
                (now,),
            )
            self._conn.commit()
            return cur.rowcount

    def users_with_warnings(self) -> list[tuple[int, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT guild_id, user_id FROM warnings"
            ).fetchall()
        return [(r["guild_id"], r["user_id"]) for r in rows]

    # ------------------------------------------------------------------
    # Timeout escalation ladder
    # ------------------------------------------------------------------
    def next_timeout_level(self, guild_id: int, user_id: int,
                           reset_days: int,
                           now: float | None = None) -> int:
        """Return the ladder rung to use (0-based) and bump the counter.

        The ladder resets to rung 0 after `reset_days` without a timeout."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT level, updated_at FROM timeout_levels"
                " WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
            if row and now - row["updated_at"] < reset_days * 86400:
                level = int(row["level"])
            else:
                level = 0
            self._conn.execute(
                "INSERT INTO timeout_levels (guild_id, user_id, level,"
                " updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(guild_id, user_id) DO UPDATE SET level=?," " updated_at=?",
                (guild_id, user_id, level + 1, now, level + 1, now),
            )
            self._conn.commit()
        return level

    def timeout_level(self, guild_id: int, user_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT level FROM timeout_levels WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
        return int(row["level"]) if row else 0

    # ------------------------------------------------------------------
    # Invite offenses (serious — never expire on the 24h wheel)
    # ------------------------------------------------------------------
    def invite_offense_count(self, guild_id: int, user_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM invite_offenses WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
        return int(row["count"]) if row else 0

    def bump_invite_offense(self, guild_id: int, user_id: int) -> int:
        """Record an invite offense and return the new total count."""
        now = time.time()
        with self._lock:
            current = self.invite_offense_count(guild_id, user_id)
            new_count = current + 1
            self._conn.execute(
                "INSERT INTO invite_offenses (guild_id, user_id, count,"
                " updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(guild_id, user_id) DO UPDATE SET count=?,"
                " updated_at=?",
                (guild_id, user_id, new_count, now, new_count, now),
            )
            self._conn.commit()
        return new_count

    # ------------------------------------------------------------------
    # Exempt (staff-approved) invite links
    # ------------------------------------------------------------------
    def add_exempt_invite(self, guild_id: int, code: str,
                          added_by: int | None) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO exempt_invites (guild_id, code,"
                " added_by, added_at) VALUES (?,?,?,?)",
                (guild_id, code, added_by, time.time()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def remove_exempt_invite(self, guild_id: int, code: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM exempt_invites WHERE guild_id=? AND code=?",
                (guild_id, code),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_exempt_invites(self, guild_id: int) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM exempt_invites WHERE guild_id=?"
                " ORDER BY added_at ASC",
                (guild_id,),
            ).fetchall()
        return list(rows)

    def is_exempt_invite(self, guild_id: int, code: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM exempt_invites WHERE guild_id=? AND code=?",
                (guild_id, code),
            ).fetchone()
        return row is not None
