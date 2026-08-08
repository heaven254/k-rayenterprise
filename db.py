"""
db.py — SQLite connection helper and schema for the K-Ray Enterprise backend.

Uses the Python standard library sqlite3 module only (no ORM dependency),
so the backend runs with nothing beyond Flask + PyJWT installed.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("KRAY_DB_PATH", os.path.join(os.path.dirname(__file__), "kray.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    business        TEXT,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    avatar_url      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_verifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code            TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    used            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    category        TEXT,
    cost            REAL NOT NULL DEFAULT 0,
    price           REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receipt_id      INTEGER,
    date            TEXT NOT NULL,
    item            TEXT NOT NULL,
    category        TEXT,
    supplier        TEXT,
    account         TEXT NOT NULL,
    qty             REAL NOT NULL,
    cost            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receipt_id      INTEGER,
    date            TEXT NOT NULL,
    item            TEXT NOT NULL,
    customer        TEXT,
    account         TEXT NOT NULL,
    qty             REAL NOT NULL,
    price           REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receipt_id      INTEGER,
    date            TEXT NOT NULL,
    customer        TEXT NOT NULL,
    item            TEXT NOT NULL,
    qty             REAL NOT NULL,
    price           REAL NOT NULL,
    total           REAL NOT NULL,
    paid            REAL NOT NULL DEFAULT 0,
    remaining       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    credit_sale_id  INTEGER NOT NULL REFERENCES credit_sales(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    amount          REAL NOT NULL,
    account         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT,
    amount          REAL NOT NULL,
    account         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    source          TEXT,
    account         TEXT NOT NULL,
    amount          REAL NOT NULL,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    from_account    TEXT NOT NULL,
    to_account      TEXT NOT NULL,
    amount          REAL NOT NULL,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS pumice (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK(type IN ('sale','purchase','expense')),
    desc            TEXT,
    qty             REAL,
    amount          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    type            TEXT NOT NULL,
    item            TEXT NOT NULL,
    qty             REAL NOT NULL,
    cost            REAL,
    comment         TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author          TEXT NOT NULL,
    text            TEXT NOT NULL,
    date            TEXT NOT NULL
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db_cursor(commit=False):
    """Context manager yielding a cursor; commits on success if commit=True."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    finally:
        conn.close()


def row_to_dict(row):
    return dict(row) if row is not None else None


def rows_to_list(rows):
    return [dict(r) for r in rows]
