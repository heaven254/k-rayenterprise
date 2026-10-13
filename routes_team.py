"""
routes_team.py — who can use the app.

  GET    /api/team                 members + pending invites (any logged-in user)
  POST   /api/team/invite          owner: allow an email to join (and email them)
  DELETE /api/team/invite          owner: withdraw an invite
  POST   /api/team/users/<id>/access   owner: {"enabled": true|false} switch someone's access off/on

Owner = emails in KRAY_OWNER_EMAILS, otherwise the very first account created.
Removing access never deletes a person's records; it only stops them logging in.
"""
import re
from flask import Blueprint, request, jsonify, g

from db import db_cursor, log_activity
from auth import login_required, forget_disabled, owner_ids, get_setting
import mailer

bp = Blueprint("team", __name__, url_prefix="/api/team")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


_owner_ids = owner_ids


def _need_owner(cur):
    if g.user_id not in _owner_ids(cur):
        return jsonify({"error": "Only the business owner can do this."}), 403
    return None


@bp.get("")
@login_required
def team():
    with db_cursor() as cur:
        owners = _owner_ids(cur)
        cur.execute("SELECT id, name, email, email_verified, disabled, summary_email, created_at FROM users ORDER BY id")
        users = cur.fetchall()
        cur.execute("SELECT email, added_by, added_at FROM allowed_emails ORDER BY added_at")
        invites = cur.fetchall()
    have = {u["email"].lower() for u in users}
    return jsonify({
        "me_is_owner": g.user_id in owners,
        "members": [{
            "id": u["id"], "name": u["name"], "email": u["email"],
            "is_owner": u["id"] in owners, "disabled": bool(u["disabled"]), "summary": bool(u["summary_email"]),
            "joined": u["created_at"].isoformat() if u["created_at"] else None,
        } for u in users],
        "invites": [{"email": i["email"], "added_by": i["added_by"],
                     "added_at": i["added_at"].isoformat() if i["added_at"] else None}
                    for i in invites if i["email"].lower() not in have],
    })


@bp.post("/invite")
@login_required
def invite():
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    with db_cursor(commit=True) as cur:
        denied = _need_owner(cur)
        if denied:
            return denied
        cur.execute("SELECT id FROM users WHERE LOWER(email) = %s", (email,))
        if cur.fetchone():
            return jsonify({"error": "That person already has an account."}), 409
        cur.execute("INSERT INTO allowed_emails (email, added_by) VALUES (%s, %s) ON CONFLICT DO NOTHING", (email, g.name))
        log_activity(cur, g.user_id, g.name, "created", "team invite", None, email)
    emailed = False
    if not mailer.DEMO_MODE:
        link = request.host_url.rstrip("/")
        emailed = mailer.send_email(
            email, "You're invited to K-Ray Enterprise",
            f"{g.name} invited you to K-Ray Enterprise.\n\nOpen {link} and either click \"Sign in with Google\" "
            f"or sign up with this email address ({email}). You will get a 6-digit code to confirm it.")
    return jsonify({"invited": email, "emailed": emailed}), 201


@bp.delete("/invite")
@login_required
def withdraw_invite():
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    with db_cursor(commit=True) as cur:
        denied = _need_owner(cur)
        if denied:
            return denied
        cur.execute("DELETE FROM allowed_emails WHERE email = %s", (email,))
        log_activity(cur, g.user_id, g.name, "deleted", "team invite", None, email)
    return jsonify({"removed": email})


@bp.post("/users/<int:user_id>/access")
@login_required
def set_access(user_id):
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    with db_cursor(commit=True) as cur:
        denied = _need_owner(cur)
        if denied:
            return denied
        if user_id in _owner_ids(cur):
            return jsonify({"error": "The owner's access can't be switched off."}), 400
        cur.execute("UPDATE users SET disabled = %s WHERE id = %s RETURNING name, email", (not enabled, user_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "User not found."}), 404
        log_activity(cur, g.user_id, g.name, "updated", "team access", user_id,
                     f"{row['email']} {'enabled' if enabled else 'disabled'}")
    forget_disabled(user_id)
    return jsonify({"id": user_id, "disabled": not enabled})


@bp.post("/users/<int:user_id>/summary")
@login_required
def set_summary(user_id):
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    with db_cursor(commit=True) as cur:
        denied = _need_owner(cur)
        if denied:
            return denied
        cur.execute("UPDATE users SET summary_email = %s WHERE id = %s RETURNING email", (enabled, user_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "User not found."}), 404
        log_activity(cur, g.user_id, g.name, "updated", "team summary email", user_id,
                     f"{row['email']} daily summary {'on' if enabled else 'off'}")
    return jsonify({"id": user_id, "summary": enabled})


@bp.get("/settings")
@login_required
def get_settings():
    return jsonify({"staff_can_delete": get_setting("staff_can_delete", "true") != "false"})


@bp.put("/settings")
@login_required
def put_settings():
    data = request.get_json(silent=True) or {}
    with db_cursor(commit=True) as cur:
        denied = _need_owner(cur)
        if denied:
            return denied
        if "staff_can_delete" in data:
            val = "true" if data["staff_can_delete"] else "false"
            cur.execute("INSERT INTO settings (key, value) VALUES ('staff_can_delete', %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (val,))
            log_activity(cur, g.user_id, g.name, "updated", "team settings", None,
                         "staff can delete records: " + val)
    return jsonify({"staff_can_delete": get_setting("staff_can_delete", "true") != "false"})


@bp.get("/logins")
@login_required
def recent_logins():
    """Recent sign-in attempts (owner only): who, how, from where, success or not."""
    with db_cursor() as cur:
        denied = _need_owner(cur)
        if denied:
            return denied
        cur.execute("SELECT email, method, success, reason, ip, user_agent, at FROM login_log ORDER BY id DESC LIMIT 40")
        rows = cur.fetchall()
    return jsonify([{
        "email": r["email"], "method": r["method"], "success": r["success"], "reason": r["reason"],
        "ip": r["ip"], "device": (r["user_agent"] or "")[:90], "at": r["at"].isoformat() + "Z",
    } for r in rows])
