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
}
# tables without a user_id column
NO_USER = {"credit_payments"}
# delete children first, insert parents first
DELETE_ORDER = ["credit_payments", "credit_sales", "sales", "purchases", "products",
                "expenses", "cash", "transfers", "pumice", "stock_logs", "comments"]
INSERT_ORDER = ["products", "purchases", "sales", "credit_sales", "credit_payments",
                "expenses", "cash", "transfers", "pumice", "stock_logs", "comments"]


@bp.get("/backup")
@login_required
def backup():
    out = {}
    with db_cursor() as cur:
        for table, cols in TABLES.items():
            cur.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id")
            out[table] = rows_to_list(cur.fetchall())
    return jsonify({
        "app": "k-ray-enterprise",
        "version": 1,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "created_by": g.name,
        "counts": {t: len(r) for t, r in out.items()},
        "tables": out,
    })


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
