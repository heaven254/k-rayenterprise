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
        return fn(*args, **kwargs)
    return wrapper
