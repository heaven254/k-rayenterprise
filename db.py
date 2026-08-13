"""
db.py — PostgreSQL connection helper and schema for the K-Ray Enterprise
backend.

Uses psycopg2 with Render's auto-provided DATABASE_URL environment
variable when you attach a Render Postgres database to this service.
Falls back to KRAY_DATABASE_URL if you're running this elsewhere.

Rows are returned as plain dicts (via RealDictCursor) so the rest of
the codebase can keep using row['column_name'] access exactly like it
did with sqlite3.Row.
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("KRAY_DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "No database configured. Set DATABASE_URL (Render provides this "
        "automatically when you attach a Postgres database to this "
        "service) or KRAY_DATABASE_URL."
    )

# Render's DATABASE_URL sometimes starts with 'postgres://' — psycopg2
# accepts both, but normalize just in case other tools are stricter.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    business        TEXT,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    avatar_url      TEXT,
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS verifications (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code            TEXT NOT NULL,
    purpose         TEXT NOT NULL CHECK(purpose IN ('signup','admin_login')),
    expires_at      TIMESTAMP NOT NULL,
    used            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    category        TEXT,
    cost            DOUBLE PRECISION NOT NULL DEFAULT 0,
    price           DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchases (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receipt_id      INTEGER,
    date            TEXT NOT NULL,
    item            TEXT NOT NULL,
    category        TEXT,
    supplier        TEXT,
    account         TEXT NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    cost            DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receipt_id      INTEGER,
    date            TEXT NOT NULL,
    item            TEXT NOT NULL,
    customer        TEXT,
    account         TEXT NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    price           DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_sales (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receipt_id      INTEGER,
    date            TEXT NOT NULL,
    customer        TEXT NOT NULL,
    item            TEXT NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    total           DOUBLE PRECISION NOT NULL,
    paid            DOUBLE PRECISION NOT NULL DEFAULT 0,
    remaining       DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_payments (
    id              SERIAL PRIMARY KEY,
    credit_sale_id  INTEGER NOT NULL REFERENCES credit_sales(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    amount          DOUBLE PRECISION NOT NULL,
    account         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT,
    amount          DOUBLE PRECISION NOT NULL,
    account         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    source          TEXT,
    account         TEXT NOT NULL,
    amount          DOUBLE PRECISION NOT NULL,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS transfers (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    from_account    TEXT NOT NULL,
    to_account      TEXT NOT NULL,
    amount          DOUBLE PRECISION NOT NULL,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS pumice (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK(type IN ('sale','purchase','expense')),
    item_desc       TEXT,
    qty             DOUBLE PRECISION,
    amount          DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    type            TEXT NOT NULL,
    item            TEXT NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    cost            DOUBLE PRECISION,
    comment         TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author          TEXT NOT NULL,
    text            TEXT NOT NULL,
    date            TEXT NOT NULL
);
"""


# Lightweight migrations for columns/tables added after a database was
# already created by an earlier version of this schema. Each statement
# is safe to run repeatedly (IF NOT EXISTS everywhere).
MIGRATIONS = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;
"""


def get_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(MIGRATIONS)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db_cursor(commit=False):
    """Context manager yielding a dict-cursor; commits on success if commit=True."""
    conn = get_connection()
    try:
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
