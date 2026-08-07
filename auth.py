"""
auth.py — password hashing, JWT issuing/verification, and role-based
access-control decorators.

Roles:
  - "user"  : can create/read/update records for their own business, but
              cannot delete anything.
  - "admin" : can read and DELETE any record, but cannot create/update
              anything. Reaching admin role requires a second factor
              (a one-time email code) on top of the normal password login.
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


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def issue_token(user_id: int, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
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
    """Any authenticated user or admin may proceed. Sets g.user_id, g.email, g.role."""
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

        g.user_id = payload["sub"]
        g.email = payload["email"]
        g.role = payload["role"]
        return fn(*args, **kwargs)
    return wrapper


def write_required(fn):
    """Only the 'user' role may create/update records. Admin is delete-only."""
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

        g.user_id = payload["sub"]
        g.email = payload["email"]
        g.role = payload["role"]

        if g.role != "user":
            return jsonify({
                "error": "Admin accounts can only delete records. "
                         "Log in as a regular user to add or edit data."
            }), 403
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    """Only the 'admin' role may delete records."""
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

        g.user_id = payload["sub"]
        g.email = payload["email"]
        g.role = payload["role"]

        if g.role != "admin":
            return jsonify({
                "error": "Only an Admin can delete this. Log out and sign back "
                         "in with Admin access (email verification required)."
            }), 403
        return fn(*args, **kwargs)
    return wrapper
