"""
guards.py — who may do what, and the "closed day" lock.

Runs before every write (POST/PUT/DELETE) to the records API:
  * Owner-only actions: balance counts (reconciliations + their adjustments),
    morning counts, Pumice settlement, restoring a backup, closing/reopening days.
  * Staff edits can be switched off by the owner (staff_can_edit); deletes are
    handled in auth.py (staff_can_delete).
  * Closed days: records dated on or before `locked_until` cannot be added,
    changed or deleted. Staff are always stopped; the owner can go ahead only
    by giving a reason (X-Override-Reason header), which is written to the log.
  Credit repayments are never blocked (money coming in later for an old sale).
"""
import re
from flask import Blueprint, request, jsonify, g
from auth import (login_required, owner_required, get_setting, decode_token,
                  _extract_token, user_state)
from db import db_cursor, log_activity

bp = Blueprint("guards", __name__, url_prefix="/api")

# url segment -> (table, date column)
DATED = {
    "purchases": ("purchases", "date"), "sales": ("sales", "date"),
    "expenses": ("expenses", "date"), "cash": ("cash", "date"),
    "transfers": ("transfers", "date"), "pumice": ("pumice", "date"),
    "pumice-credit": ("pumice_credit", "date"), "credit-sales": ("credit_sales", "date"),
    "day-openings": ("day_openings", "date"),
}
OWNER_ONLY_URLS = {"day-openings", "reconciliations"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _who():
    tok = _extract_token()
    if not tok:
        return None
    try:
        p = decode_token(tok)
        uid = int(p["sub"])
    except Exception:
        return None
    disabled, owner = user_state(uid)
    if disabled:
        return None
    return uid, owner, (p.get("name") or p.get("email"))


def _row(table, rid):
    with db_cursor() as cur:
        cur.execute(f"SELECT * FROM {table} WHERE id = %s", (rid,))
        return cur.fetchone()


def check_write():
    """Called before each request. Returns a (response, status) to block, or None."""
    if request.method not in ("POST", "PUT", "DELETE"):
        return None
    path = request.path
    if not path.startswith("/api/"):
        return None
    parts = [p for p in path[5:].split("/") if p]
    if not parts:
        return None
    who = _who()
    if who is None:
        return None                       # login_required will answer
    uid, owner, name = who
    first = parts[0]
    body = request.get_json(silent=True) or {}
    method = request.method

    if first == "restore" and not owner:
        return jsonify({"error": "Only the business owner can restore a backup."}), 403

    if first not in DATED and first != "reconciliations":
        return None
    is_payment = first == "credit-sales" and len(parts) > 2 and parts[2] == "payments"
    rid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    table, dcol = DATED.get(first, ("reconciliations", "date"))
    existing = _row(table, rid) if rid and not is_payment else None

    # ---- who can do what ------------------------------------------------
    if not owner:
        why = None
        if first in OWNER_ONLY_URLS:
            why = "Only the business owner can change balance counts and morning counts."
        src = (body.get("source") or (existing or {}).get("source") or "")
        if first == "cash" and str(src).startswith("Reconciliation"):
            why = "Only the business owner can post or change balance adjustments."
        desc = body.get("desc") or (existing or {}).get("item_desc") or ""
        if first == "pumice" and str(desc).startswith("✅ Settlement"):
            why = "Only the business owner can settle or change a Pumice settlement."
        if why:
            return jsonify({"error": why}), 403
        if method == "PUT" and get_setting("staff_can_edit", "true") == "false":
            return jsonify({"error": "Only the business owner can edit records."}), 403

    # ---- closed-day lock -----------------------------------------------
    if is_payment:
        return None
    if first == "pumice-credit" and method == "PUT" and set(body.keys()) <= {"paid"}:
        return None
    locked = get_setting("locked_until", "") or ""
    if not locked:
        return None
    dates = []
    if method in ("POST", "PUT") and DATE_RE.match(str(body.get("date") or "")):
        dates.append(str(body["date"])[:10])
    if existing is not None and existing.get(dcol):
        dates.append(str(existing[dcol])[:10])
    hit = [d for d in dates if d <= locked]
    if not hit:
        return None
    reason = (request.headers.get("X-Override-Reason") or "").strip()
    if owner and len(reason) >= 3:
        try:
            with db_cursor(commit=True) as cur:
                log_activity(cur, uid, name, "override", table, rid,
                             f"closed day {hit[0]} changed ({method}). Reason: {reason[:200]}")
        except Exception:
            pass
        return None
    if owner:
        return jsonify({"error": f"{hit[0]} is a closed day. Give a reason to change it.",
                        "closed_day": True, "needs_reason": True}), 423
    return jsonify({"error": f"{hit[0]} is a closed day. Ask the business owner to change it.",
                    "closed_day": True}), 423


@bp.get("/day-lock")
@login_required
def get_lock():
    return jsonify({"locked_until": get_setting("locked_until", "") or ""})


@bp.post("/day-lock")
@owner_required
def set_lock():
    data = request.get_json(silent=True) or {}
    val = str(data.get("locked_until") or "")[:10]
    reason = (data.get("reason") or "").strip()
    if val and not DATE_RE.match(val):
        return jsonify({"error": "Invalid date."}), 400
    old = get_setting("locked_until", "") or ""
    if val < old and len(reason) < 3:      # reopening needs a reason
        return jsonify({"error": "Give a reason to reopen a closed day."}), 400
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO settings (key, value) VALUES ('locked_until', %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (val,))
        log_activity(cur, g.user_id, g.name, "updated", "closed days", None,
                     f"books closed up to {val or 'none'} (was {old or 'none'})"
                     + (f". Reason: {reason[:200]}" if reason else ""))
    return jsonify({"locked_until": val})


@bp.post("/report/email")
@login_required
def email_report():
    """Send a report the app built (html) to the user's own email or a team member's."""
    from mailer import send_email, last_error
    data = request.get_json(silent=True) or {}
    html = data.get("html") or ""
    subject = (data.get("subject") or "K-Ray report")[:150]
    text = (data.get("text") or "K-Ray report — open this email in an app that shows HTML.")[:20000]
    to = (data.get("to") or g.email).strip().lower()
    if len(html) > 400000:
        return jsonify({"error": "Report is too large."}), 400
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE LOWER(email) = %s AND disabled = FALSE", (to,))
        if not cur.fetchone():
            return jsonify({"error": "That email is not on your team."}), 400
    if not send_email(to, subject, text, html=html):
        return jsonify({"error": "Email could not be sent: " + str(last_error() or "unknown")}), 502
    return jsonify({"sent_to": to})
