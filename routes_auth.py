\
"""Auth routes: signup, login, email verify, admin OTP, forgot password."""
import os
import secrets
import time
import hashlib
import smtplib
from email.message import EmailMessage
from functools import wraps

from flask import Blueprint, request, jsonify, g
import jwt

from db import db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

JWT_SECRET = os.environ.get("KRAY_JWT_SECRET", "kray-dev-secret-change-me-32chars!!")
JWT_HOURS = int(os.environ.get("KRAY_JWT_HOURS", "168"))
CODE_TTL = int(os.environ.get("KRAY_CODE_TTL", "600"))  # seconds
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@kray.local")
APP_NAME = "K-Ray Enterprise"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return f"{salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return secrets.compare_digest(test, digest)


def make_token(user_row, role=None):
    payload = {
        "sub": str(user_row["id"]),
        "email": user_row["email"],
        "role": role or user_row["role"] or "user",
        "exp": int(time.time()) + JWT_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def user_public(row):
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "business": row["business"] or "",
        "email_verified": bool(row["email_verified"]),
    }


def send_email(to: str, subject: str, body: str) -> bool:
    """Send real email when SMTP is configured; otherwise log only."""
    if not SMTP_HOST or not SMTP_USER:
        print(f"[email-demo] To={to} Subject={subject}\n{body}")
        return False
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return True


def create_verification(email: str, purpose: str):
    code = f"{secrets.randbelow(10**6):06d}"
    vid = secrets.token_urlsafe(16)
    now = time.time()
    with db() as conn:
        conn.execute(
            "INSERT INTO verifications (id, email, code, purpose, created_at, expires_at, used) VALUES (?,?,?,?,?,?,0)",
            (vid, email, code, purpose, now, now + CODE_TTL),
        )
    sent = send_email(
        email,
        f"{APP_NAME} verification code",
        f"Your verification code is: {code}\n\nIt expires in {CODE_TTL // 60} minutes.\n",
    )
    result = {"verification_id": vid, "requires_verification": True}
    if not sent:
        result["demo_code"] = code  # only when SMTP not configured
        result["message"] = "Demo mode: code shown in UI (SMTP not configured)."
    else:
        result["message"] = "Verification code sent to your email."
    return result


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth[7:]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except Exception:
            return jsonify({"error": "Invalid or expired token"}), 401
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (int(payload["sub"]),)).fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 401
        g.user = row
        g.role = payload.get("role") or row["role"]
        return fn(*args, **kwargs)

    return wrapper


@bp.post("/signup")
def signup():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    business = (data.get("business") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not name or not email or len(password) < 8:
        return jsonify({"error": "Name, email, and password (8+ chars) are required."}), 400
    with db() as conn:
        exists = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            return jsonify({"error": "An account with this email already exists."}), 409
        conn.execute(
            "INSERT INTO users (email, name, business, password_hash, role, email_verified) VALUES (?,?,?,?, 'user', 0)",
            (email, name, business, hash_password(password)),
        )
    ver = create_verification(email, "signup")
    return jsonify(ver), 201


@bp.post("/verify-email")
def verify_email():
    data = request.get_json(force=True, silent=True) or {}
    vid = data.get("verification_id")
    code = (data.get("code") or "").strip()
    if not vid or not code:
        return jsonify({"error": "verification_id and code are required."}), 400
    with db() as conn:
        row = conn.execute("SELECT * FROM verifications WHERE id=?", (vid,)).fetchone()
        if not row or row["used"]:
            return jsonify({"error": "Invalid verification."}), 400
        if time.time() > row["expires_at"]:
            return jsonify({"error": "Code expired. Request a new one."}), 400
        if not secrets.compare_digest(row["code"], code):
            return jsonify({"error": "Incorrect code."}), 400
        conn.execute("UPDATE verifications SET used=1 WHERE id=?", (vid,))
        conn.execute("UPDATE users SET email_verified=1 WHERE email=?", (row["email"],))
        user = conn.execute("SELECT * FROM users WHERE email=?", (row["email"],)).fetchone()
    token = make_token(user, "user")
    return jsonify({"token": token, "user": user_public(user), "role": "user"})


@bp.post("/resend-verification")
def resend_verification():
    data = request.get_json(force=True, silent=True) or {}
    vid = data.get("verification_id")
    with db() as conn:
        row = conn.execute("SELECT * FROM verifications WHERE id=?", (vid,)).fetchone()
    if not row:
        return jsonify({"error": "Unknown verification."}), 404
    ver = create_verification(row["email"], row["purpose"])
    return jsonify(ver)


@bp.post("/login")
def login():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = (data.get("role") or "user").strip().lower()
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user or not check_password(password, user["password_hash"]):
        return jsonify({"error": "Invalid email or password."}), 401

    if not user["email_verified"]:
        ver = create_verification(email, "signup")
        return jsonify({**ver, "error": None, "message": "Email not verified yet. Enter the code we sent."})

    if role == "admin":
        ver = create_verification(email, "admin")
        return jsonify({**ver, "user": user_public(user)})

    token = make_token(user, "user")
    return jsonify({"token": token, "user": user_public(user), "role": "user"})


@bp.post("/verify-admin")
def verify_admin():
    data = request.get_json(force=True, silent=True) or {}
    vid = data.get("verification_id")
    code = (data.get("code") or "").strip()
    with db() as conn:
        row = conn.execute("SELECT * FROM verifications WHERE id=?", (vid,)).fetchone()
        if not row or row["used"] or row["purpose"] != "admin":
            return jsonify({"error": "Invalid verification."}), 400
        if time.time() > row["expires_at"]:
            return jsonify({"error": "Code expired."}), 400
        if not secrets.compare_digest(row["code"], code):
            return jsonify({"error": "Incorrect code."}), 400
        conn.execute("UPDATE verifications SET used=1 WHERE id=?", (vid,))
        user = conn.execute("SELECT * FROM users WHERE email=?", (row["email"],)).fetchone()
    token = make_token(user, "admin")
    return jsonify({"token": token, "user": user_public(user), "role": "admin"})


@bp.post("/resend-admin-code")
def resend_admin_code():
    data = request.get_json(force=True, silent=True) or {}
    vid = data.get("verification_id")
    with db() as conn:
        row = conn.execute("SELECT * FROM verifications WHERE id=?", (vid,)).fetchone()
    if not row:
        return jsonify({"error": "Unknown verification."}), 404
    ver = create_verification(row["email"], "admin")
    return jsonify(ver)


@bp.post("/forgot-password")
def forgot_password():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    # Always generic response (no email enumeration)
    if user:
        ver = create_verification(email, "reset")
        return jsonify({"message": "If an account exists, a reset code was sent.", **{k: ver[k] for k in ver if k in ("demo_code", "verification_id")}})
    return jsonify({"message": "If an account exists, a reset code was sent."})


@bp.get("/me")
@require_auth
def me():
    return jsonify({"user": user_public(g.user), "role": g.role})
