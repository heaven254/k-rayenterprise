"""
routes_auth.py — signup, login (user or admin), admin email-code
verification, and the "who am I" endpoint.
"""
import random
import datetime
from flask import Blueprint, request, jsonify, g

from db import db_cursor
from auth import hash_password, verify_password, issue_token, login_required
from mailer import send_admin_code, DEMO_MODE

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

CODE_TTL_MINUTES = 5


def _user_public(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "business": row["business"],
        "email": row["email"],
        "avatarUrl": row["avatar_url"],
    }


@bp.post("/signup")
def signup():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    business = (data.get("business") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return jsonify({"error": "name, email and password are required"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cur.fetchone():
            return jsonify({"error": "An account with this email already exists"}), 409

        cur.execute(
            "INSERT INTO users (name, business, email, password_hash) VALUES (?, ?, ?, ?)",
            (name, business, email, hash_password(password)),
        )
        user_id = cur.lastrowid
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()

    token = issue_token(user_id, email, "user")
    return jsonify({"token": token, "role": "user", "user": _user_public(user)}), 201


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = data.get("role") or "user"
    if role not in ("user", "admin"):
        role = "user"

    with db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        return jsonify({"error": "No matching account found. Check your details or sign up."}), 401

    if role == "user":
        token = issue_token(user["id"], user["email"], "user")
        return jsonify({"token": token, "role": "user", "user": _user_public(user)})

    # role == admin -> issue a one-time code instead of a token
    code = f"{random.randint(0, 999999):06d}"
    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(minutes=CODE_TTL_MINUTES)).isoformat()

    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO admin_verifications (user_id, code, expires_at) VALUES (?, ?, ?)",
            (user["id"], code, expires_at),
        )
        verification_id = cur.lastrowid

    sent_by_email = send_admin_code(user["email"], code)

    resp = {
        "requires_verification": True,
        "verification_id": verification_id,
        "email": user["email"],
        "demo_mode": DEMO_MODE,
    }
    if not sent_by_email:
        # Demo mode: surface the code directly so the flow is testable
        # without any SMTP setup, mirroring the front-end prototype.
        resp["demo_code"] = code

    return jsonify(resp)


@bp.post("/verify-admin")
def verify_admin():
    data = request.get_json(silent=True) or {}
    verification_id = data.get("verification_id")
    code = str(data.get("code") or "").strip()

    if not verification_id or not code:
        return jsonify({"error": "verification_id and code are required"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM admin_verifications WHERE id = ?", (verification_id,))
        v = cur.fetchone()

        if not v:
            return jsonify({"error": "Verification request not found"}), 404
        if v["used"]:
            return jsonify({"error": "This code has already been used. Please log in again."}), 400
        if datetime.datetime.fromisoformat(v["expires_at"]) < datetime.datetime.utcnow():
            return jsonify({"error": "Code expired. Please request a new one."}), 400
        if v["code"] != code:
            return jsonify({"error": "Incorrect code. Please try again."}), 401

        cur.execute("UPDATE admin_verifications SET used = 1 WHERE id = ?", (verification_id,))
        cur.execute("SELECT * FROM users WHERE id = ?", (v["user_id"],))
        user = cur.fetchone()

    token = issue_token(user["id"], user["email"], "admin")
    return jsonify({"token": token, "role": "admin", "user": _user_public(user)})


@bp.post("/resend-admin-code")
def resend_admin_code():
    data = request.get_json(silent=True) or {}
    verification_id = data.get("verification_id")
    if not verification_id:
        return jsonify({"error": "verification_id is required"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM admin_verifications WHERE id = ?", (verification_id,))
        v = cur.fetchone()
        if not v:
            return jsonify({"error": "Verification request not found"}), 404

        code = f"{random.randint(0, 999999):06d}"
        expires_at = (datetime.datetime.utcnow() + datetime.timedelta(minutes=CODE_TTL_MINUTES)).isoformat()
        cur.execute(
            "UPDATE admin_verifications SET code = ?, expires_at = ?, used = 0 WHERE id = ?",
            (code, expires_at, verification_id),
        )
        cur.execute("SELECT * FROM users WHERE id = ?", (v["user_id"],))
        user = cur.fetchone()

    sent_by_email = send_admin_code(user["email"], code)
    resp = {"requires_verification": True, "verification_id": verification_id, "demo_mode": DEMO_MODE}
    if not sent_by_email:
        resp["demo_code"] = code
    return jsonify(resp)


@bp.get("/me")
@login_required
def me():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = ?", (g.user_id,))
        user = cur.fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"user": _user_public(user), "role": g.role})
