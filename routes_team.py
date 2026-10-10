"""
routes_team.py — who can use the app.

  GET    /api/team                 members + pending invites (any logged-in user)
  POST   /api/team/invite          owner: allow an email to join (and email them)
  DELETE /api/team/invite          owner: withdraw an invite
  POST   /api/team/users/<id>/access   owner: {"enabled": true|false} switch someone's access off/on

Owner = emails in KRAY_OWNER_EMAILS, otherwise the very first account created.
Removing access never deletes a person's records; it only stops them logging in.
"""
import os
import re
from flask import Blueprint, request, jsonify, g

from db import db_cursor, log_activity
from auth import login_required, forget_disabled
import mailer

bp = Blueprint("team", __name__, url_prefix="/api/team")

OWNER_EMAILS = {e.strip().lower() for e in os.environ.get("KRAY_OWNER_EMAILS", "").split(",") if e.strip()}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _owner_ids(cur):
    if OWNER_EMAILS:
        cur.execute("SELECT id FROM users WHERE LOWER(email) = ANY(%s)", (list(OWNER_EMAILS),))
        ids = {r["id"] for r in cur.fetchall()}
        if ids:
            return ids
    cur.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    r = cur.fetchone()
    return {r["id"]} if r else set()


def _need_owner(cur):
    if g.user_id not in _owner_ids(cur):
        return jsonify({"error": "Only the business owner can do this."}), 403
    return None


@bp.get("")
@login_required
def team():
    with db_cursor() as cur:
        owners = _owner_ids(cur)
        cur.execute("SELECT id, name, email, email_verified, disabled, created_at FROM users ORDER BY id")
        users = cur.fetchall()
        cur.execute("SELECT email, added_by, added_at FROM allowed_emails ORDER BY added_at")
        invites = cur.fetchall()
    have = {u["email"].lower() for u in users}
    return jsonify({
        "me_is_owner": g.user_id in owners,
        "members": [{
            "id": u["id"], "name": u["name"], "email": u["email"],
            "is_owner": u["id"] in owners, "disabled": bool(u["disabled"]),
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
