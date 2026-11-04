"""Fail-closed, SQLite-backed GPU coordination for this ACHEWIW instance.

Coordinates ACHEWIW text jobs, ComfyUI image requests and prompt refining
across concurrent HTTP requests. Cannot exclusively control OTHER host apps;
real Ollama model occupancy is separately checked via /api/ps.
"""
from __future__ import annotations

import sqlite3
import time

LEASE_SECONDS = 600


def init_gpu_leases(connection: sqlite3.Connection) -> None:
    connection.execute("""
    CREATE TABLE IF NOT EXISTS achewiw_gpu_lease (
        name TEXT PRIMARY KEY CHECK(name='gpu'),
        token TEXT NOT NULL,
        purpose TEXT NOT NULL,
        expires_at REAL NOT NULL
    )
    """)


def image_operation_active(connection: sqlite3.Connection, *, at: float | None = None) -> bool:
    now = time.time() if at is None else at
    row = connection.execute(
        "SELECT token FROM achewiw_gpu_lease WHERE name='gpu' AND expires_at>?",
        (now,),
    ).fetchone()
    return row is not None


def reserve_gpu(connection: sqlite3.Connection, token: str, purpose: str,
                *, at: float | None = None) -> bool:
    """Call within BEGIN IMMEDIATE; false means the GPU is already reserved."""
    now = time.time() if at is None else at
    connection.execute("DELETE FROM achewiw_gpu_lease WHERE expires_at<=?", (now,))
    if image_operation_active(connection, at=now):
        return False
    connection.execute(
        "INSERT INTO achewiw_gpu_lease(name,token,purpose,expires_at) VALUES ('gpu',?,?,?)",
        (token, purpose, now + LEASE_SECONDS),
    )
    return True


def release_gpu(connection: sqlite3.Connection, token: str) -> None:
    connection.execute(
        "DELETE FROM achewiw_gpu_lease WHERE name='gpu' AND token=?", (token,)
    )
