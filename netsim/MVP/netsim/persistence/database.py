"""SQLite simulation history.

One table, created on demand. The engine knows nothing about this module;
the application layer records results after each run.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from ..simulation.result import SimulationResult

DEFAULT_DB_FILENAME = "netsim_history.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS simulations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    source_name       TEXT    NOT NULL,
    source_ip         TEXT,
    destination_name  TEXT    NOT NULL,
    destination_ip    TEXT,
    protocol          TEXT    NOT NULL,
    destination_port  INTEGER,
    success           INTEGER NOT NULL,
    failure_reason    TEXT,
    failure_message   TEXT,
    blocked_by        TEXT,
    path_devices      TEXT    NOT NULL,
    log               TEXT    NOT NULL
);
"""


@dataclass
class HistoryRow:
    id: int
    timestamp: str
    source_name: str
    destination_name: str
    protocol: str
    destination_port: Optional[int]
    success: bool
    failure_reason: Optional[str]
    failure_message: Optional[str]
    blocked_by: Optional[str]
    path_devices: List[str]
    log: List[str]

    def summary(self) -> str:
        status = "OK  " if self.success else "FAIL"
        port = "" if self.destination_port is None else ":%d" % self.destination_port
        return "#%d %s %s %s -> %s (%s%s)%s" % (
            self.id,
            self.timestamp,
            status,
            self.source_name,
            self.destination_name,
            self.protocol.upper(),
            port,
            "" if self.success else "  " + (self.failure_message or ""),
        )


class SimulationHistory:
    """Thin wrapper around a SQLite file. Safe to construct repeatedly."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(os.getcwd(), DEFAULT_DB_FILENAME)
        self._connection: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    # -- internals ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def _ensure_schema(self) -> None:
        connection = self._connect()
        with connection:
            connection.executescript(SCHEMA)

    # -- public API --------------------------------------------------------
    def record(self, result: SimulationResult) -> int:
        """Store one simulation result and return its row id."""
        connection = self._connect()
        blocked_by = result.blocked_by_device_id or result.failure_device_id
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO simulations (
                    timestamp, source_name, source_ip, destination_name, destination_ip,
                    protocol, destination_port, success, failure_reason, failure_message,
                    blocked_by, path_devices, log
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    result.source_name,
                    result.source_ip,
                    result.destination_name,
                    result.destination_ip,
                    result.protocol,
                    result.destination_port,
                    1 if result.success else 0,
                    result.failure_reason.value,
                    result.failure_message,
                    blocked_by,
                    json.dumps(result.path_devices),
                    json.dumps(result.log_lines()),
                ),
            )
        return int(cursor.lastrowid)

    def recent(self, limit: int = 20) -> List[HistoryRow]:
        connection = self._connect()
        rows = connection.execute(
            "SELECT * FROM simulations ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [self._to_row(row) for row in rows]

    def count(self) -> int:
        connection = self._connect()
        return int(connection.execute("SELECT COUNT(*) FROM simulations").fetchone()[0])

    def clear(self) -> None:
        connection = self._connect()
        with connection:
            connection.execute("DELETE FROM simulations")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @staticmethod
    def _to_row(row: sqlite3.Row) -> HistoryRow:
        return HistoryRow(
            id=row["id"],
            timestamp=row["timestamp"],
            source_name=row["source_name"],
            destination_name=row["destination_name"],
            protocol=row["protocol"],
            destination_port=row["destination_port"],
            success=bool(row["success"]),
            failure_reason=row["failure_reason"],
            failure_message=row["failure_message"],
            blocked_by=row["blocked_by"],
            path_devices=json.loads(row["path_devices"]),
            log=json.loads(row["log"]),
        )
