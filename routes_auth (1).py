"""
routes_auth.py — signup (with email verification), login (rate
limited), password reset, and the "who am I" endpoint.

Every account must verify its email once (right after signup) before
it can log in. There's no separate admin role — once logged in, every
account has full access to the shared business data.
"""
import os
import json
import hashlib
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
    record_login, is_owner,
)
from mailer import send_verification_code, DEMO_MODE, last_error

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

CODE_TTL_MINUTES = 5

# --- Google sign-in -------------------------------------------------------
GOOGLE_CLIENT_ID = os.environ.get("KRAY_GOOGLE_CLIENT_ID", "").strip()
# Optional comma-separated list of emails allowed to join (sign up or use Google).
# If empty, existing accounts can use Google and signup stays open as before.
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("KRAY_ALLOWED_EMAILS", "").split(",") if e.strip()}


def allowed_set():
    """Emails allowed to join: the Render list plus anyone the owner invited in the app."""
    with db_cursor() as cur:
        cur.execute("SELECT email FROM allowed_emails")
        invited = {r["email"].lower() for r in cur.fetchall()}
    return ALLOWED_EMAILS | invited


def signup_restricted():
    return bool(allowed_set())


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


LOGIN_CODE_ENABLED = os.environ.get("KRAY_LOGIN_CODE", "on").strip().lower() != "off"
MAX_CODE_ATTEMPTS = 5


def _device_hash(device_id):
    """Hash of the random id the browser keeps. Too-short ids are ignored."""
    device_id = str(device_id or "").strip()
    if len(device_id) < 16:
        return None
    return hashlib.sha256(device_id.encode("utf-8")).hexdigest()


def _is_trusted(user_id, device_hash):
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM trusted_devices WHERE user_id = %s AND device_hash = %s", (user_id, device_hash))
        return cur.fetchone() is not None


def _touch_device(user_id, device_hash):
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE trusted_devices SET last_seen = NOW() WHERE user_id = %s AND device_hash = %s", (user_id, device_hash))
    except Exception:
        pass


def _trust_device(cur, user_id, device_hash, user_agent):
    cur.execute(
        "INSERT INTO trusted_devices (user_id, device_hash, label) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, device_hash) DO UPDATE SET last_seen = NOW()",
        (user_id, device_hash, (user_agent or "")[:200]),
    )


def _user_public(row):
    return {
        "isOwner": is_owner(row["id"]),
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
    allowed = allowed_set()
    if allowed and email not in allowed:
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
        record_login(user["id"] if user else None, email, "password", False, "wrong password or no account")
        return jsonify({"error": "No matching account found. Check your details or sign up."}), 401

    clear_rate_limit(email)

    if user.get("disabled"):
        record_login(user["id"], email, "password", False, "account disabled")
        return jsonify({"error": "This account has been disabled. Ask the business owner."}), 403

    if not user["email_verified"]:
        # Email was never confirmed after signup — send a fresh code
        # instead of letting them log in.
        with db_cursor(commit=True) as cur:
            verification_id, code = _create_verification(cur, user["id"], "signup")
        sent_by_email = send_verification_code(user["email"], code, purpose="signup")
        resp = _verification_response(verification_id, user["email"], code, sent_by_email)
        resp["reason"] = "email_not_verified"
        return jsonify(resp)

    # --- New-device check: an unfamiliar phone/computer must enter a code emailed to the account ---
    device_hash = _device_hash(data.get("device_id"))
    if LOGIN_CODE_ENABLED and not DEMO_MODE:
        if not device_hash:
            record_login(user["id"], email, "password", False, "no device id")
            return jsonify({"error": "Please refresh the page and try again."}), 400
        if not _is_trusted(user["id"], device_hash):
            with db_cursor(commit=True) as cur:
                verification_id, code = _create_verification(cur, user["id"], "login")
            if send_verification_code(user["email"], code, purpose="login"):
                record_login(user["id"], email, "password", False, "new device: code sent")
                return jsonify({"requires_verification": True, "verification_id": verification_id,
                                "email": user["email"], "demo_mode": False, "reason": "new_device"})
            # Email could not be sent: don't lock the owner out of their own business; log it loudly.
            print(f"[LOGIN] could not email sign-in code to {user['email']}: {last_error()}")
            record_login(user["id"], email, "password", True, "code email failed - allowed in")
            token = issue_token(user["id"], user["email"], user["name"])
            return jsonify({"token": token, "user": _user_public(user)})
        _touch_device(user["id"], device_hash)

    record_login(user["id"], email, "password", True, "")
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
        if user and user.get("disabled"):
            return jsonify({"error": "This account has been disabled. Ask the business owner."}), 403
        if not user:
            if email not in allowed_set():
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

    record_login(user["id"], user["email"], "google", True, "")
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
        if v["purpose"] not in ("signup", "login"):
            return jsonify({"error": "Verification request not found"}), 404
        if v["used"]:
            return jsonify({"error": "This code has already been used. Please log in again."}), 400
        if v["expires_at"] < datetime.datetime.utcnow():
            return jsonify({"error": "Code expired. Please request a new one."}), 400
        if v["code"] != code:
            tries = (v.get("attempts") or 0) + 1
            if tries >= MAX_CODE_ATTEMPTS:
                cur.execute("UPDATE verifications SET attempts = %s, used = TRUE WHERE id = %s", (tries, verification_id))
                conn_msg = "Too many wrong codes. Please request a new code."
                err = (jsonify({"error": conn_msg}), 429)
            else:
                cur.execute("UPDATE verifications SET attempts = %s WHERE id = %s", (tries, verification_id))
                err = (jsonify({"error": f"Incorrect code. {MAX_CODE_ATTEMPTS - tries} tries left."}), 401)
            wrong = err
        else:
            wrong = None
        if wrong is None:
            cur.execute("UPDATE verifications SET used = TRUE WHERE id = %s", (verification_id,))
            cur.execute("UPDATE users SET email_verified = TRUE WHERE id = %s", (v["user_id"],))
            cur.execute("SELECT * FROM users WHERE id = %s", (v["user_id"],))
            user = cur.fetchone()
            dh = _device_hash(data.get("device_id"))
            if dh:
                _trust_device(cur, user["id"], dh, request.headers.get("User-Agent"))
    if wrong is not None:
        return wrong

    record_login(user["id"], user["email"], "password+code" if v["purpose"] == "login" else "signup", True, "")
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
            "UPDATE verifications SET code = %s, expires_at = %s, used = FALSE, attempts = 0 WHERE id = %s",
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
            tries = (v.get("attempts") or 0) + 1
            cur.execute("UPDATE verifications SET attempts = %s, used = %s WHERE id = %s",
                        (tries, tries >= MAX_CODE_ATTEMPTS, verification_id))
            cur.connection.commit()
            if tries >= MAX_CODE_ATTEMPTS:
                return jsonify({"error": "Too many wrong codes. Please request a new one."}), 429
            return jsonify({"error": f"Incorrect code. {MAX_CODE_ATTEMPTS - tries} tries left."}), 401

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
