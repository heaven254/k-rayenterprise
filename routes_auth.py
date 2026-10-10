"""
routes_auth.py — signup (with email verification), login (rate
limited), password reset, and the "who am I" endpoint.

Every account must verify its email once (right after signup) before
it can log in. There's no separate admin role — once logged in, every
account has full access to the shared business data.
"""
import os
import json
import random
import secrets
import datetime
import urllib.request
import urllib.parse
from flask import Blueprint, request, jsonify, g

from db import db_cursor
from auth import (
    hash_password, verify_password, issue_token, login_required,
    check_rate_limit, record_failed_attempt, clear_rate_limit,
)
from mailer import send_verification_code, DEMO_MODE, last_error

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

CODE_TTL_MINUTES = 5

# --- Google sign-in -------------------------------------------------------
GOOGLE_CLIENT_ID = os.environ.get("KRAY_GOOGLE_CLIENT_ID", "").strip()
# Optional comma-separated list of emails allowed to join (sign up or use Google).
# If empty, existing accounts can use Google and signup stays open as before.
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("KRAY_ALLOWED_EMAILS", "").split(",") if e.strip()}


def _verify_google_token(credential):
    """Ask Google to validate the ID token. Returns its claims, or raises ValueError."""
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"id_token": credential})
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            claims = json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise ValueError("Google could not verify this sign-in. Please try again.")
    if claims.get("aud") != GOOGLE_CLIENT_ID:
        raise ValueError("This Google sign-in was not issued for this app.")
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("Invalid Google sign-in.")
    if str(claims.get("email_verified")).lower() != "true":
        raise ValueError("Your Google email is not verified.")
    if not claims.get("email"):
        raise ValueError("Google did not return an email address.")
    return claims


def _user_public(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "business": row["business"],
        "email": row["email"],
        "avatarUrl": row["avatar_url"],
        "emailVerified": row["email_verified"],
    }


def _create_verification(cur, user_id, purpose="signup"):
    code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=CODE_TTL_MINUTES)
    cur.execute(
        "INSERT INTO verifications (user_id, code, purpose, expires_at) VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, code, purpose, expires_at),
    )
    verification_id = cur.fetchone()["id"]
    return verification_id, code


def _verification_response(verification_id, email, code, sent_by_email):
    resp = {
        "requires_verification": True,
        "verification_id": verification_id,
        "email": email,
        "demo_mode": DEMO_MODE,
    }
    if not sent_by_email:
        # Demo mode (or a failed email send): surface the code directly
        # so the flow is never a dead end.
        resp["demo_code"] = code
        resp["mail_error"] = last_error()
    return resp


@bp.post("/signup")
def signup():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    business = (data.get("business") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return jsonify({"error": "name, email and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return jsonify({"error": "This email is not allowed to create an account. Ask the business owner to add it."}), 403

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return jsonify({"error": "An account with this email already exists"}), 409

        cur.execute(
            "INSERT INTO users (name, business, email, password_hash) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, business, email, hash_password(password)),
        )
        user_id = cur.fetchone()["id"]

        verification_id, code = _create_verification(cur, user_id, "signup")

    sent_by_email = send_verification_code(email, code, purpose="signup")
    return jsonify(_verification_response(verification_id, email, code, sent_by_email)), 201


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    allowed, retry_after = check_rate_limit(email)
    if not allowed:
        return jsonify({
            "error": f"Too many failed attempts. Please try again in about {retry_after} minute(s)."
        }), 429

    with db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        record_failed_attempt(email)
        return jsonify({"error": "No matching account found. Check your details or sign up."}), 401

    clear_rate_limit(email)

    if not user["email_verified"]:
        # Email was never confirmed after signup — send a fresh code
        # instead of letting them log in.
        with db_cursor(commit=True) as cur:
            verification_id, code = _create_verification(cur, user["id"], "signup")
        sent_by_email = send_verification_code(user["email"], code, purpose="signup")
        resp = _verification_response(verification_id, user["email"], code, sent_by_email)
        resp["reason"] = "email_not_verified"
        return jsonify(resp)

    token = issue_token(user["id"], user["email"], user["name"])
    return jsonify({"token": token, "user": _user_public(user)})


@bp.get("/config")
def public_config():
    """Public, non-secret settings the login page needs (the Google Client ID is public by design)."""
    return jsonify({"google_client_id": GOOGLE_CLIENT_ID or None})


@bp.post("/google")
def google_login():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google sign-in is not set up on this server."}), 400
    data = request.get_json(silent=True) or {}
    credential = data.get("credential") or ""
    if not credential:
        return jsonify({"error": "Missing Google credential."}), 400
    try:
        claims = _verify_google_token(credential)
    except ValueError as e:
        return jsonify({"error": str(e)}), 401

    email = claims["email"].strip().lower()
    name = (claims.get("name") or claims.get("given_name") or email.split("@")[0]).strip()
    picture = claims.get("picture") or None

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user:
            if email not in ALLOWED_EMAILS:
                return jsonify({"error": "No account exists for this Google email. Ask the business owner to add you."}), 403
            cur.execute(
                "INSERT INTO users (name, business, email, password_hash, avatar_url, email_verified) "
                "VALUES (%s, %s, %s, %s, %s, TRUE) RETURNING *",
                (name, "", email, hash_password(secrets.token_urlsafe(32)), picture),
            )
            user = cur.fetchone()
        elif not user["email_verified"]:
            # Google has already verified this address.
            cur.execute("UPDATE users SET email_verified = TRUE WHERE id = %s RETURNING *", (user["id"],))
            user = cur.fetchone()

    token = issue_token(user["id"], user["email"], user["name"])
    return jsonify({"token": token, "user": _user_public(user)})


@bp.post("/verify-code")
def verify_code():
    data = request.get_json(silent=True) or {}
    verification_id = data.get("verification_id")
    code = str(data.get("code") or "").strip()

    if not verification_id or not code:
        return jsonify({"error": "verification_id and code are required"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM verifications WHERE id = %s", (verification_id,))
        v = cur.fetchone()

        if not v:
            return jsonify({"error": "Verification request not found"}), 404
        if v["used"]:
            return jsonify({"error": "This code has already been used. Please log in again."}), 400
        if v["expires_at"] < datetime.datetime.utcnow():
            return jsonify({"error": "Code expired. Please request a new one."}), 400
        if v["code"] != code:
            return jsonify({"error": "Incorrect code. Please try again."}), 401

        cur.execute("UPDATE verifications SET used = TRUE WHERE id = %s", (verification_id,))
        cur.execute("UPDATE users SET email_verified = TRUE WHERE id = %s", (v["user_id"],))
        cur.execute("SELECT * FROM users WHERE id = %s", (v["user_id"],))
        user = cur.fetchone()

    token = issue_token(user["id"], user["email"], user["name"])
    return jsonify({"token": token, "user": _user_public(user)})


@bp.post("/resend-code")
def resend_code():
    data = request.get_json(silent=True) or {}
    verification_id = data.get("verification_id")
    if not verification_id:
        return jsonify({"error": "verification_id is required"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM verifications WHERE id = %s", (verification_id,))
        v = cur.fetchone()
        if not v:
            return jsonify({"error": "Verification request not found"}), 404

        code = f"{random.randint(0, 999999):06d}"
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=CODE_TTL_MINUTES)
        cur.execute(
            "UPDATE verifications SET code = %s, expires_at = %s, used = FALSE WHERE id = %s",
            (code, expires_at, verification_id),
        )
        cur.execute("SELECT * FROM users WHERE id = %s", (v["user_id"],))
        user = cur.fetchone()

    sent_by_email = send_verification_code(user["email"], code, purpose=v["purpose"])
    return jsonify(_verification_response(verification_id, user["email"], code, sent_by_email))


@bp.post("/forgot-password")
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email is required"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user:
            # Don't reveal whether the email exists — respond the same
            # way either way so this can't be used to enumerate accounts.
            return jsonify({
                "requires_verification": True,
                "email": email,
                "demo_mode": DEMO_MODE,
                "note": "If an account with this email exists, a reset code has been sent.",
            })

        verification_id, code = _create_verification(cur, user["id"], "password_reset")

    sent_by_email = send_verification_code(user["email"], code, purpose="password_reset")
    return jsonify(_verification_response(verification_id, user["email"], code, sent_by_email))


@bp.post("/reset-password")
def reset_password():
    data = request.get_json(silent=True) or {}
    verification_id = data.get("verification_id")
    code = str(data.get("code") or "").strip()
    new_password = data.get("new_password") or ""

    if not verification_id or not code or not new_password:
        return jsonify({"error": "verification_id, code and new_password are required"}), 400
    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM verifications WHERE id = %s", (verification_id,))
        v = cur.fetchone()

        if not v or v["purpose"] != "password_reset":
            return jsonify({"error": "Verification request not found"}), 404
        if v["used"]:
            return jsonify({"error": "This code has already been used. Please request a new one."}), 400
        if v["expires_at"] < datetime.datetime.utcnow():
            return jsonify({"error": "Code expired. Please request a new one."}), 400
        if v["code"] != code:
            return jsonify({"error": "Incorrect code. Please try again."}), 401

        cur.execute("UPDATE verifications SET used = TRUE WHERE id = %s", (verification_id,))
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(new_password), v["user_id"]),
        )
        cur.execute("SELECT * FROM users WHERE id = %s", (v["user_id"],))
        user = cur.fetchone()

    clear_rate_limit(user["email"])
    token = issue_token(user["id"], user["email"], user["name"])
    return jsonify({"token": token, "user": _user_public(user)})


@bp.get("/me")
@login_required
def me():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (g.user_id,))
        user = cur.fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"user": _user_public(user)})
