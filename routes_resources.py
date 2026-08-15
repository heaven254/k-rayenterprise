"""
routes_resources.py — CRUD endpoints for every business record type:
products, purchases, sales, credit sales & payments, expenses, cash,
transfers, pumice, stock logs, comments — plus the shared activity log.

This is a single shared business used by a small team — every logged-in
user can see and edit every record, regardless of who created it.
user_id is still stored on each row (who created it, for reference),
but never used to restrict access. Every create/update/delete is
recorded in activity_log so the team can see who did what.
"""
from flask import Blueprint, request, jsonify, g

from db import db_cursor, rows_to_list, row_to_dict, log_activity
from auth import login_required

bp = Blueprint("resources", __name__, url_prefix="/api")


def _num(data, key, default=0):
    try:
        return float(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _row_out(row, reverse_map):
    """Apply column->API field renames (e.g. item_desc -> desc) to an outgoing row."""
    if row is None:
        return None
    d = dict(row)
    for col, api_field in reverse_map.items():
        if col in d:
            d[api_field] = d.pop(col)
    return d


def _build_summary(row_out, fields, skip=("receipt_id", "date")):
    """Short human-readable description of a record for the activity log."""
    parts = []
    for f in fields:
        if f in skip:
            continue
        val = row_out.get(f)
        if val is None or val == "":
            continue
        parts.append(f"{f}: {val}")
        if len(parts) >= 3:
            break
    return ", ".join(parts) if parts else f"record #{row_out.get('id')}"


# ---------------------------------------------------------------------
# Generic helpers for the "simple" resources: same shape of
# list / create / update / delete, differing only in table name + fields.
# ---------------------------------------------------------------------
def register_simple_resource(url, table, fields, numeric_fields=(), column_map=None):
    """
    Registers GET/POST/PUT/DELETE for a straightforward shared table.

    fields: ordered list of API field names accepted from the JSON body
    numeric_fields: subset of `fields` that should be coerced to float
    column_map: optional {api_field: actual_column_name} for fields whose
                DB column name differs from the API's JSON field name
                (used to dodge reserved-word column names like "desc")
    """
    column_map = column_map or {}
    reverse_map = {v: k for k, v in column_map.items()}
    db_columns = [column_map.get(f, f) for f in fields]

    def list_records():
        with db_cursor() as cur:
            cur.execute(f"SELECT * FROM {table} ORDER BY id DESC")
            rows = cur.fetchall()
        return jsonify([_row_out(r, reverse_map) for r in rows])

    def create_record():
        data = request.get_json(silent=True) or {}
        values = []
        for f in fields:
            values.append(_num(data, f) if f in numeric_fields else data.get(f))
        columns = ", ".join(["user_id"] + db_columns)
        placeholders = ", ".join(["%s"] * (len(db_columns) + 1))
        with db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING *",
                [g.user_id] + values,
            )
            row = cur.fetchone()
            row_out = _row_out(row, reverse_map)
            log_activity(cur, g.user_id, g.name, "created", table, row_out["id"],
                         _build_summary(row_out, fields))
        return jsonify(row_out), 201

    def update_record(record_id):
        data = request.get_json(silent=True) or {}
        with db_cursor(commit=True) as cur:
            cur.execute(f"SELECT id FROM {table} WHERE id = %s", (record_id,))
            if not cur.fetchone():
                return jsonify({"error": "Record not found"}), 404

            set_clauses = []
            values = []
            for f in fields:
                if f in data:
                    col = column_map.get(f, f)
                    set_clauses.append(f"{col} = %s")
                    values.append(_num(data, f) if f in numeric_fields else data.get(f))
            if not set_clauses:
                return jsonify({"error": "No editable fields provided"}), 400

            values.append(record_id)
            cur.execute(
                f"UPDATE {table} SET {', '.join(set_clauses)} WHERE id = %s RETURNING *",
                values,
            )
            row = cur.fetchone()
            row_out = _row_out(row, reverse_map)
            log_activity(cur, g.user_id, g.name, "updated", table, record_id,
                         _build_summary(row_out, fields))
        return jsonify(row_out)

    def delete_record(record_id):
        with db_cursor(commit=True) as cur:
            cur.execute(f"SELECT * FROM {table} WHERE id = %s", (record_id,))
            existing = cur.fetchone()
            if not existing:
                return jsonify({"error": "Record not found"}), 404
            summary = _build_summary(_row_out(existing, reverse_map), fields)
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (record_id,))
            log_activity(cur, g.user_id, g.name, "deleted", table, record_id, summary)
        return jsonify({"deleted": record_id})

    list_records.__name__ = f"list_{table}"
    create_record.__name__ = f"create_{table}"
    update_record.__name__ = f"update_{table}"
    delete_record.__name__ = f"delete_{table}"

    bp.get(f"/{url}")(login_required(list_records))
    bp.post(f"/{url}")(login_required(create_record))
    bp.put(f"/{url}/<int:record_id>")(login_required(update_record))
    bp.delete(f"/{url}/<int:record_id>")(login_required(delete_record))


register_simple_resource(
    "purchases", "purchases",
    fields=["receipt_id", "date", "item", "category", "supplier", "account", "qty", "cost"],
    numeric_fields=("receipt_id", "qty", "cost"),
)

register_simple_resource(
    "sales", "sales",
    fields=["receipt_id", "date", "item", "customer", "account", "qty", "price"],
    numeric_fields=("receipt_id", "qty", "price"),
)

register_simple_resource(
    "expenses", "expenses",
    fields=["date", "name", "category", "amount", "account"],
    numeric_fields=("amount",),
)

register_simple_resource(
    "cash", "cash",
    fields=["date", "source", "account", "amount", "note"],
    numeric_fields=("amount",),
)

register_simple_resource(
    "transfers", "transfers",
    fields=["date", "from_account", "to_account", "amount", "note"],
    numeric_fields=("amount",),
)

register_simple_resource(
    "pumice", "pumice",
    fields=["date", "type", "desc", "qty", "amount"],
    numeric_fields=("qty", "amount"),
    column_map={"desc": "item_desc"},
)

register_simple_resource(
    "stock-logs", "stock_logs",
    fields=["date", "type", "item", "qty", "cost", "comment"],
    numeric_fields=("qty", "cost"),
)

register_simple_resource(
    "comments", "comments",
    fields=["author", "text", "date"],
)


# ---------------------------------------------------------------------
# Products — POST create + PUT update (name/category/cost/price) + DELETE.
# ---------------------------------------------------------------------
@bp.get("/products")
@login_required
def list_products():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM products ORDER BY id DESC")
        rows = cur.fetchall()
    return jsonify(rows_to_list(rows))


@bp.post("/products")
@login_required
def create_product():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Product name is required"}), 400
    category = data.get("category") or "General"
    cost = _num(data, "cost")
    price = _num(data, "price")

    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO products (user_id, name, category, cost, price) VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (g.user_id, name, category, cost, price),
        )
        row = cur.fetchone()
        log_activity(cur, g.user_id, g.name, "created", "products", row["id"],
                     f"name: {row['name']}, price: {row['price']}")
    return jsonify(row_to_dict(row)), 201


@bp.put("/products/<int:product_id>")
@login_required
def update_product(product_id):
    """Accepts any of name/category/cost/price. Also supports the legacy
    {field, value} shape used for the inline price/cost quick-edit."""
    data = request.get_json(silent=True) or {}

    if "field" in data and data.get("field") in ("cost", "price"):
        data = {data["field"]: data.get("value")}

    editable = {"name", "category", "cost", "price"}
    numeric = {"cost", "price"}
    set_clauses = []
    values = []
    for f in editable:
        if f in data:
            set_clauses.append(f"{f} = %s")
            values.append(_num(data, f) if f in numeric else data.get(f))

    if not set_clauses:
        return jsonify({"error": "No editable fields provided"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM products WHERE id = %s", (product_id,))
        if not cur.fetchone():
            return jsonify({"error": "Product not found"}), 404
        values.append(product_id)
        cur.execute(f"UPDATE products SET {', '.join(set_clauses)} WHERE id = %s RETURNING *", values)
        row = cur.fetchone()
        log_activity(cur, g.user_id, g.name, "updated", "products", product_id,
                     f"name: {row['name']}, price: {row['price']}")
    return jsonify(row_to_dict(row))


@bp.delete("/products/<int:product_id>")
@login_required
def delete_product(product_id):
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        existing = cur.fetchone()
        if not existing:
            return jsonify({"error": "Product not found"}), 404
        cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
        log_activity(cur, g.user_id, g.name, "deleted", "products", product_id, f"name: {existing['name']}")
    return jsonify({"deleted": product_id})


# ---------------------------------------------------------------------
# Credit sales + repayments — creating a payment must update the
# parent credit sale's paid/remaining totals, so these get dedicated
# routes rather than the generic helper.
# ---------------------------------------------------------------------
@bp.get("/credit-sales")
@login_required
def list_credit_sales():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM credit_sales ORDER BY id DESC")
        sales = rows_to_list(cur.fetchall())

        for sale in sales:
            cur.execute(
                "SELECT * FROM credit_payments WHERE credit_sale_id = %s ORDER BY id",
                (sale["id"],),
            )
            sale["payments"] = rows_to_list(cur.fetchall())

    return jsonify(sales)


@bp.post("/credit-sales")
@login_required
def create_credit_sale():
    data = request.get_json(silent=True) or {}
    customer = (data.get("customer") or "").strip()
    item = (data.get("item") or "").strip()
    if not customer or not item:
        return jsonify({"error": "customer and item are required"}), 400

    qty = _num(data, "qty")
    price = _num(data, "price")
    total = qty * price
    receipt_id = data.get("receipt_id")

    with db_cursor(commit=True) as cur:
        cur.execute(
            """INSERT INTO credit_sales
               (user_id, receipt_id, date, customer, item, qty, price, total, paid, remaining)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s) RETURNING *""",
            (g.user_id, receipt_id, data.get("date"), customer, item, qty, price, total, total),
        )
        row = row_to_dict(cur.fetchone())
        log_activity(cur, g.user_id, g.name, "created", "credit_sales", row["id"],
                     f"customer: {customer}, item: {item}, total: {total}")
    row["payments"] = []
    return jsonify(row), 201


@bp.post("/credit-sales/<int:credit_sale_id>/payments")
@login_required
def add_credit_payment(credit_sale_id):
    data = request.get_json(silent=True) or {}
    amount = _num(data, "amount")
    account = data.get("account") or "cash"
    date = data.get("date")

    if amount <= 0:
        return jsonify({"error": "Payment amount must be greater than zero"}), 400

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM credit_sales WHERE id = %s", (credit_sale_id,))
        sale = cur.fetchone()
        if not sale:
            return jsonify({"error": "Credit sale not found"}), 404

        new_paid = sale["paid"] + amount
        new_remaining = max(0.0, sale["total"] - new_paid)

        cur.execute(
            "INSERT INTO credit_payments (credit_sale_id, date, amount, account) VALUES (%s, %s, %s, %s)",
            (credit_sale_id, date, amount, account),
        )
        cur.execute(
            "UPDATE credit_sales SET paid = %s, remaining = %s WHERE id = %s RETURNING *",
            (new_paid, new_remaining, credit_sale_id),
        )
        updated = row_to_dict(cur.fetchone())
        cur.execute(
            "SELECT * FROM credit_payments WHERE credit_sale_id = %s ORDER BY id",
            (credit_sale_id,),
        )
        updated["payments"] = rows_to_list(cur.fetchall())
        log_activity(cur, g.user_id, g.name, "payment", "credit_sales", credit_sale_id,
                     f"customer: {sale['customer']}, paid: {amount}")

    return jsonify(updated), 201


@bp.delete("/credit-sales/<int:credit_sale_id>")
@login_required
def delete_credit_sale(credit_sale_id):
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM credit_sales WHERE id = %s", (credit_sale_id,))
        existing = cur.fetchone()
        if not existing:
            return jsonify({"error": "Credit sale not found"}), 404
        cur.execute("DELETE FROM credit_sales WHERE id = %s", (credit_sale_id,))
        log_activity(cur, g.user_id, g.name, "deleted", "credit_sales", credit_sale_id,
                     f"customer: {existing['customer']}, item: {existing['item']}")
    return jsonify({"deleted": credit_sale_id})


# ---------------------------------------------------------------------
# Activity log — read-only feed of recent create/update/delete actions
# across the whole shared business.
# ---------------------------------------------------------------------
@bp.get("/activity")
@login_required
def list_activity():
    limit = min(int(request.args.get("limit", 100)), 300)
    with db_cursor() as cur:
        cur.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    return jsonify(rows_to_list(rows))
