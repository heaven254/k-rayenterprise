"""
routes_backup.py — full-data backup and restore for the shared business.

GET  /api/backup   -> every business table as JSON (download as a file)
POST /api/restore  -> REPLACES all business data with the uploaded backup,
                      in one transaction (all-or-nothing). User accounts and
                      the activity log are never touched.
"""
import datetime
from flask import Blueprint, request, jsonify, g
import psycopg2.extras

from db import db_cursor, rows_to_list, log_activity
from auth import login_required

bp = Blueprint("backup", __name__, url_prefix="/api")

# table -> columns that are backed up / restored (user_id is set on restore)
TABLES = {
    "products":    ["id", "name", "category", "cost", "price"],
    "purchases":   ["id", "receipt_id", "date", "item", "category", "supplier", "account", "qty", "cost"],
    "sales":       ["id", "receipt_id", "date", "item", "customer", "account", "qty", "price"],
    "credit_sales": ["id", "receipt_id", "date", "customer", "item", "qty", "price", "total", "paid", "remaining"],
    "credit_payments": ["id", "credit_sale_id", "date", "amount", "account"],
    "expenses":    ["id", "date", "name", "category", "amount", "account"],
    "cash":        ["id", "date", "source", "account", "amount", "note"],
    "transfers":   ["id", "date", "from_account", "to_account", "amount", "note"],
    "pumice":      ["id", "date", "type", "item_desc", "qty", "amount"],
    "stock_logs":  ["id", "date", "type", "item", "qty", "cost", "comment"],
    "comments":    ["id", "author", "text", "date"],
    "reconciliations": ["id", "date", "account", "expected", "counted", "difference", "note", "checked_by", "adjusted"],
}
# tables without a user_id column
NO_USER = {"credit_payments"}
# delete children first, insert parents first
DELETE_ORDER = ["credit_payments", "credit_sales", "sales", "purchases", "products",
                "expenses", "cash", "transfers", "pumice", "stock_logs", "comments", "reconciliations"]
INSERT_ORDER = ["products", "purchases", "sales", "credit_sales", "credit_payments",
                "expenses", "cash", "transfers", "pumice", "stock_logs", "comments", "reconciliations"]


def build_backup(created_by):
    out = {}
    with db_cursor() as cur:
        for table, cols in TABLES.items():
            cur.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id")
            out[table] = rows_to_list(cur.fetchall())
    return {
        "app": "k-ray-enterprise",
        "version": 1,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "created_by": created_by,
        "counts": {t: len(r) for t, r in out.items()},
        "tables": out,
    }


@bp.get("/backup")
@login_required
def backup():
    return jsonify(build_backup(g.name))


@bp.post("/restore")
@login_required
def restore():
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "REPLACE":
        return jsonify({"error": "Restore not confirmed."}), 400
    tables = data.get("tables")
    if data.get("app") != "k-ray-enterprise" or not isinstance(tables, dict):
        return jsonify({"error": "This is not a valid K-Ray backup file."}), 400
    for t in TABLES:
        if t in tables and not isinstance(tables[t], list):
            return jsonify({"error": f"Backup is damaged: '{t}' is not a list."}), 400

    restored = {}
    try:
        with db_cursor(commit=True) as cur:
            for t in DELETE_ORDER:
                cur.execute(f"DELETE FROM {t}")
            for t in INSERT_ORDER:
                cols = TABLES[t]
                rows = tables.get(t) or []
                restored[t] = len(rows)
                if not rows:
                    continue
                insert_cols = cols if t in NO_USER else ["user_id"] + cols
                values = []
                for r in rows:
                    row = [r.get(c) for c in cols]
                    values.append(row if t in NO_USER else [g.user_id] + row)
                psycopg2.extras.execute_values(
                    cur,
                    f"INSERT INTO {t} ({', '.join(insert_cols)}) VALUES %s",
                    values,
                )
            # keep auto-increment counters ahead of the restored ids
            for t in TABLES:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)"
                )
            log_activity(cur, g.user_id, g.name, "restored", "backup", None,
                         "Restored from backup: " + ", ".join(f"{t} {n}" for t, n in restored.items() if n))
    except Exception as e:  # whole restore is rolled back
        return jsonify({"error": "Restore failed and nothing was changed: " + str(e)}), 400
    return jsonify({"restored": restored})


# ---------------------------------------------------------------------------
# Weekly emailed backup
# Runs from the health check (like the daily summary). Env vars (optional):
#   KRAY_BACKUP_EMAIL  "off" to disable (default on)
#   KRAY_BACKUP_DAY    0=Mon ... 6=Sun (default 6, Sunday)
#   KRAY_BACKUP_HOUR   hour 0-23 Nairobi time (default 21)
#   KRAY_BACKUP_TO     comma-separated recipients (default: all verified users)
# ---------------------------------------------------------------------------
import os, json, time, threading
import mailer

B_ENABLED = os.environ.get("KRAY_BACKUP_EMAIL", "on").strip().lower() != "off"
B_DAY = int(os.environ.get("KRAY_BACKUP_DAY", "6") or 6)
B_HOUR = int(os.environ.get("KRAY_BACKUP_HOUR", "21") or 21)
B_TO = [e.strip() for e in os.environ.get("KRAY_BACKUP_TO", "").split(",") if e.strip()]
_b_sent_day = None
_b_last_attempt = 0.0
_b_lock = threading.Lock()


def _nairobi_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=3)


def _recipients():
    if B_TO:
        return B_TO
    with db_cursor() as cur:
        cur.execute("SELECT email FROM users WHERE email_verified = TRUE ORDER BY id")
        return [r["email"] for r in cur.fetchall()]


def email_backup_to(emails, who="automatic weekly backup"):
    data = build_backup(who)
    raw = json.dumps(data).encode("utf-8")
    day = _nairobi_now().strftime("%Y-%m-%d")
    name = f"kray-backup-{day}.json"
    counts = data["counts"]
    summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items() if v) or "no records yet"
    subject = f"K-Ray backup — {day}"
    body = (f"Attached is a full backup of K-Ray Enterprise ({summary}).\n\n"
            f"Keep this email safe. To restore: open the app > Profile > Backup & Restore > choose this file.\n"
            f"Restoring replaces all current data, so only do it if something went wrong.")
    ok = 0
    for e in emails:
        if mailer.send_email(e, subject, body, None, [(name, raw)]):
            ok += 1
    return ok, len(emails)


def maybe_send_weekly_backup():
    global _b_sent_day, _b_last_attempt
    if not B_ENABLED or mailer.DEMO_MODE:
        return
    now = _nairobi_now()
    day = now.strftime("%Y-%m-%d")
    if now.weekday() != B_DAY or now.hour < B_HOUR or _b_sent_day == day:
        return
    if time.time() - _b_last_attempt < 1800:
        return
    if not _b_lock.acquire(blocking=False):
        return
    try:
        _b_last_attempt = time.time()
        with db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO backup_email_log (day) VALUES (%s) ON CONFLICT DO NOTHING RETURNING day", (day,))
            claimed = cur.fetchone() is not None
        if not claimed:
            _b_sent_day = day
            return
        emails = _recipients()
        ok, total = email_backup_to(emails) if emails else (0, 0)
        if total and ok == 0:
            with db_cursor(commit=True) as cur:
                cur.execute("DELETE FROM backup_email_log WHERE day = %s", (day,))
            print("[BACKUP] emailing failed for all recipients; will retry")
        else:
            with db_cursor(commit=True) as cur:
                cur.execute("UPDATE backup_email_log SET recipients = %s WHERE day = %s", (ok, day))
            _b_sent_day = day
            print(f"[BACKUP] weekly backup for {day} emailed to {ok}/{total}")
    except Exception as e:
        print(f"[BACKUP ERROR] {e}")
    finally:
        _b_lock.release()


def trigger_in_background():
    now = _nairobi_now()
    if (not B_ENABLED or mailer.DEMO_MODE or now.weekday() != B_DAY or now.hour < B_HOUR
            or _b_sent_day == now.strftime("%Y-%m-%d")):
        return
    threading.Thread(target=maybe_send_weekly_backup, daemon=True).start()


@bp.get("/backup/status")
@login_required
def backup_status():
    with db_cursor() as cur:
        cur.execute("SELECT day FROM backup_email_log WHERE recipients > 0 ORDER BY day DESC LIMIT 1")
        row = cur.fetchone()
    return jsonify({
        "email_ready": not mailer.DEMO_MODE,
        "weekly_enabled": B_ENABLED and not mailer.DEMO_MODE,
        "last_emailed": row["day"] if row else None,
        "weekday": B_DAY, "hour": B_HOUR,
    })


@bp.post("/backup/email-now")
@login_required
def backup_email_now():
    """Email a full backup to the logged-in user only."""
    if mailer.DEMO_MODE:
        return jsonify({"error": "Email is not set up on the server yet."}), 400
    with db_cursor() as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (g.user_id,))
        row = cur.fetchone()
    ok, _ = email_backup_to([row["email"]], g.name)
    if not ok:
        return jsonify({"error": "Could not send: " + (mailer.last_error() or "unknown error")}), 502
    return jsonify({"sent_to": row["email"]})
