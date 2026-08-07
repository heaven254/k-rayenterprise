"""
db.py — database connection helper for K-Ray Enterprise.

Production (Render):
  Set DATABASE_URL to the Render PostgreSQL Internal Database URL.

Local development:
  If DATABASE_URL is absent, the original SQLite database is used.
"""
import os
import re
import sqlite3
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get(
    "KRAY_DB_PATH",
    os.path.join(os.path.dirname(__file__), "kray.db"),
)

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
    supplier        TEXT,
    account         TEXT NOT NULL,
    qty             REAL NOT NULL,
    price           REAL NOT NULL
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
    category        TEXT,
    description     TEXT,
    amount          REAL NOT NULL,
    account         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
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
    type            TEXT NOT NULL CHECK(type IN ('sale','cost')),
    qty             REAL NOT NULL,
    price            REAL NOT NULL,
    amount          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    date            TEXT NOT NULL,
    qty_change      REAL NOT NULL,
    reason          TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author          TEXT NOT NULL,
    text            TEXT NOT NULL,
    date            TEXT NOT NULL
);
"""


def _postgres_schema():
    """Translate the small SQLite schema to PostgreSQL syntax."""
    schema = SCHEMA
    schema = schema.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT",
        "BIGSERIAL PRIMARY KEY",
    )
    schema = schema.replace(
        "DEFAULT (datetime('now'))",
        "DEFAULT CURRENT_TIMESTAMP",
    )
    # PostgreSQL has no SQLite-style INTEGER boolean convention needed here;
    # the existing 0/1 representation is intentionally retained.
    return schema


class PostgresCursor:
    """Small compatibility wrapper so existing routes can keep using ?."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    @staticmethod
    def _convert(sql):
        # Existing backend SQL uses SQLite's ? placeholders.
        return sql.replace("?", "%s")

    def execute(self, sql, params=None):
        self.lastrowid = None
        converted = self._convert(sql)
        self._cursor.execute(converted, params)

        # Existing routes use cursor.lastrowid after INSERTs.
        if re.match(r"^\s*INSERT\b", converted, re.IGNORECASE):
            self._cursor.execute("SELECT LASTVAL() AS id")
            row = self._cursor.fetchone()
            self.lastrowid = row["id"] if row else None
        return self

    def executemany(self, sql, seq):
        self._cursor.executemany(self._convert(sql), seq)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def _get_postgres_connection():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def get_connection():
    if DATABASE_URL:
        return _get_postgres_connection()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    try:
        if DATABASE_URL:
            cur = PostgresCursor(conn.cursor())
            # Execute each CREATE TABLE separately.
            for statement in _postgres_schema().split(";"):
                statement = statement.strip()
                if statement:
                    cur.execute(statement)
            conn.commit()
            cur.close()
        else:
            conn.executescript(SCHEMA)
            conn.commit()
    finally:
        conn.close()


@contextmanager
def db_cursor(commit=False):
    """Yield a cursor; commit on success when requested, rollback on error."""
    conn = get_connection()
    try:
        if DATABASE_URL:
            cur = PostgresCursor(conn.cursor())
        else:
            cur = conn.cursor()

        yield cur

        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row):
    return dict(row) if row is not None else None


def rows_to_list(rows):
    return [dict(r) for r in rows]
