"""
auth.py — password hashing, JWT issuing/verification, and the single
access-control decorator used by every route.

Every account has full read/write/delete access to its own business's
records once logged in — there's no separate admin/user permission
split. Signup still requires confirming a one-time email code before
the account can log in, which is handled in routes_auth.py.
"""
import os
import functools
import datetime
import jwt
from flask import request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

JWT_SECRET = os.environ.get("KRAY_JWT_SECRET", "dev-secret-change-me")
JWT_ALGO = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("KRAY_JWT_EXPIRY_HOURS", "12"))

if JWT_SECRET == "dev-secret-change-me":
    print(
        "[SECURITY WARNING] KRAY_JWT_SECRET is not set — using the default "
        "development secret. Set a long random KRAY_JWT_SECRET environment "
        "variable before relying on this in production."
    )

# --- Simple in-memory rate limiting (per email) ---------------------------
# Not distributed — resets on restart and only applies within a single
# server process. Good enough to blunt casual brute-forcing on a small
# single-instance deployment like this one.
_attempt_log = {}  # email -> list of failed-attempt timestamps
RATE_LIMIT_MAX_ATTEMPTS = 6
RATE_LIMIT_WINDOW_MINUTES = 15


def check_rate_limit(email: str):
    """Returns (allowed: bool, retry_after_minutes: int)."""
    now = datetime.datetime.utcnow()
    window_start = now - datetime.timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
    attempts = [t for t in _attempt_log.get(email, []) if t > window_start]
    _attempt_log[email] = attempts
    if len(attempts) >= RATE_LIMIT_MAX_ATTEMPTS:
        oldest = min(attempts)
        retry_after = RATE_LIMIT_WINDOW_MINUTES - int((now - oldest).total_seconds() // 60)
        return False, max(1, retry_after)
    return True, 0


def record_failed_attempt(email: str):
    _attempt_log.setdefault(email, []).append(datetime.datetime.utcnow())


def clear_rate_limit(email: str):
    _attempt_log.pop(email, None)


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def issue_token(user_id: int, email: str, name: str = "") -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "iat": datetime.datetime.utcnow(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str):
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])


def _extract_token():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


OWNER_EMAILS = {e.strip().lower() for e in os.environ.get("KRAY_OWNER_EMAILS", "").split(",") if e.strip()}


def owner_ids(cur):
    """Owner = emails in KRAY_OWNER_EMAILS, otherwise the very first account created."""
    if OWNER_EMAILS:
        cur.execute("SELECT id FROM users WHERE LOWER(email) = ANY(%s)", (list(OWNER_EMAILS),))
        ids = {r["id"] for r in cur.fetchall()}
        if ids:
            return ids
    cur.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    r = cur.fetchone()
    return {r["id"]} if r else set()


_STATE_CACHE = {}   # user_id -> (checked_at, disabled, is_owner)


def user_state(user_id):
    """(disabled, is_owner), cached for 30 seconds."""
    import time
    from db import db_cursor
    now = time.time()
    hit = _STATE_CACHE.get(user_id)
    if hit and now - hit[0] < 30:
        return hit[1], hit[2]
    with db_cursor() as cur:
        cur.execute("SELECT disabled FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        owner = user_id in owner_ids(cur) if row else False
    disabled = (row is None) or bool(row["disabled"])
    _STATE_CACHE[user_id] = (now, disabled, owner)
    return disabled, owner


def is_disabled(user_id):
    return user_state(user_id)[0]


def is_owner(user_id):
    return user_state(user_id)[1]


def forget_disabled(user_id):
    _STATE_CACHE.pop(user_id, None)


def get_setting(key, default=None):
    from db import db_cursor
    with db_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cur.fetchone()
    return row["value"] if row else default


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or ""


def record_login(user_id, email, method, success, reason=""):
    """Write one line to the sign-in log. Never lets a logging problem break a login."""
    try:
        from db import db_cursor
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO login_log (user_id, email, method, success, reason, ip, user_agent) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (user_id, (email or "")[:200], method, success, reason[:200], client_ip()[:100],
                 (request.headers.get("User-Agent") or "")[:300]),
            )
    except Exception as e:
        print(f"[LOGIN LOG ERROR] {e}")


def login_required(fn):
    """Any authenticated user may proceed. Sets g.user_id, g.email."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expired, please log in again"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid session token"}), 401

        g.user_id = int(payload["sub"])
        g.email = payload["email"]
        g.name = payload.get("name") or payload["email"]
        disabled, owner = user_state(g.user_id)
        if disabled:
            return jsonify({"error": "This account has been disabled. Ask the business owner."}), 403
        g.is_owner = owner
        if request.method == "DELETE" and not owner and get_setting("staff_can_delete", "true") == "false":
            return jsonify({"error": "Only the business owner can delete records."}), 403
        return fn(*args, **kwargs)
    return wrapper


def owner_required(fn):
    """Login required, and only the business owner may proceed."""
    @functools.wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.is_owner:
            return jsonify({"error": "Only the business owner can do this."}), 403
        return fn(*args, **kwargs)
    return wrapper
