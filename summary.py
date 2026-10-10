"""
summary.py — the end-of-day summary email.

How it runs (no extra setup): the uptime pinger already visits /api/health every
few minutes. After KRAY_SUMMARY_HOUR (default 20 = 8 pm Nairobi time) the first
visit builds today's summary and emails it to every verified user, once per day
(a row in daily_summary_log stops duplicates, even with several workers).

Env vars (all optional):
  KRAY_DAILY_SUMMARY   "off" to disable (default on)
  KRAY_SUMMARY_HOUR    hour 0-23, Nairobi time (default 20)
  KRAY_SUMMARY_TO      comma-separated recipients (default: all verified users)
"""
import os
import time
import threading
import datetime
from html import escape
from flask import Blueprint, jsonify, g

from db import db_cursor
from auth import login_required
import mailer

bp = Blueprint("summary", __name__, url_prefix="/api")

EAT = datetime.timedelta(hours=3)
ENABLED = os.environ.get("KRAY_DAILY_SUMMARY", "on").strip().lower() != "off"
SUMMARY_HOUR = int(os.environ.get("KRAY_SUMMARY_HOUR", "20") or 20)
OVERRIDE_TO = [e.strip() for e in os.environ.get("KRAY_SUMMARY_TO", "").split(",") if e.strip()]

ACCOUNTS = {"pochi": "Pochi La Biashara", "mpesa": "M-Pesa", "cash": "Cash", "moneybox": "Moneybox"}

_sent_day = None
_last_attempt = 0.0
_lock = threading.Lock()


def nairobi_now():
    return datetime.datetime.utcnow() + EAT


def kes(n):
    n = float(n or 0)
    return "KSh {:,.0f}".format(n) if abs(n - round(n)) < 0.005 else "KSh {:,.2f}".format(n)


def build_summary(day):
    """All the numbers for one day (YYYY-MM-DD). Dates are stored as text, so match by prefix."""
    like = day + "%"
    with db_cursor() as cur:
        cur.execute("SELECT item, customer, account, qty, price, receipt_id FROM sales WHERE date LIKE %s", (like,))
        sales = cur.fetchall()
        cur.execute("SELECT item, customer, qty, price, total, receipt_id FROM credit_sales WHERE date LIKE %s", (like,))
        credit = cur.fetchall()
        cur.execute("SELECT amount, account FROM credit_payments WHERE date LIKE %s", (like,))
        repay = cur.fetchall()
        cur.execute("SELECT account, qty, cost FROM purchases WHERE date LIKE %s", (like,))
        purchases = cur.fetchall()
        cur.execute("SELECT name, category, amount, account FROM expenses WHERE date LIKE %s", (like,))
        expenses = cur.fetchall()
        cur.execute("SELECT type, amount FROM pumice WHERE date LIKE %s", (like,))
        pumice = cur.fetchall()
        cur.execute("SELECT COALESCE(SUM(remaining),0) AS owed, COUNT(DISTINCT customer) AS people FROM credit_sales WHERE remaining > 0.005")
        loans = cur.fetchone()

    cash_sales = sum(r["qty"] * r["price"] for r in sales)
    credit_goods = sum(r["total"] for r in credit)
    total_sales = cash_sales + credit_goods
    receipts = {("s", r["receipt_id"] or id(r)) for r in sales} | {("c", r["receipt_id"] or id(r)) for r in credit}
    total_purchases = sum(r["qty"] * r["cost"] for r in purchases)
    total_expenses = sum(r["amount"] for r in expenses)
    repaid = sum(r["amount"] for r in repay)

    money_in, money_out = {}, {}
    for r in sales:
        money_in[r["account"]] = money_in.get(r["account"], 0) + r["qty"] * r["price"]
    for r in repay:
        money_in[r["account"]] = money_in.get(r["account"], 0) + r["amount"]
    for r in purchases:
        money_out[r["account"]] = money_out.get(r["account"], 0) + r["qty"] * r["cost"]
    for r in expenses:
        money_out[r["account"]] = money_out.get(r["account"], 0) + r["amount"]

    items = {}
    for r in list(sales) + list(credit):
        amt = r["qty"] * r["price"]
        it = items.setdefault(r["item"], {"qty": 0.0, "amount": 0.0})
        it["qty"] += r["qty"]; it["amount"] += amt
    top = sorted(items.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:3]

    pm = {"sale": 0, "purchase": 0, "expense": 0, "withdrawal": 0}
    for r in pumice:
        pm[r["type"]] = pm.get(r["type"], 0) + r["amount"]

    return {
        "day": day, "receipts": len(receipts),
        "cash_sales": cash_sales, "credit_goods": credit_goods, "total_sales": total_sales,
        "purchases": total_purchases, "expenses": total_expenses, "repaid": repaid,
        "net": total_sales - total_purchases - total_expenses,
        "money_in": money_in, "money_out": money_out, "top": top,
        "pumice": pm, "pumice_any": any(pm.values()),
        "loans_owed": float(loans["owed"] or 0), "loans_people": int(loans["people"] or 0),
        "empty": not (sales or credit or repay or purchases or expenses or pumice),
    }


def render(s):
    d = datetime.date.fromisoformat(s["day"])
    pretty = d.strftime("%A %d %B %Y")
    subject = f"K-Ray daily summary — {d.strftime('%a %d %b')}: sales {kes(s['total_sales'])}"

    rows = [
        ("Sales", kes(s["total_sales"]), f"{s['receipts']} receipt(s)"),
        ("   paid now", kes(s["cash_sales"]), ""),
        ("   on credit (debt)", kes(s["credit_goods"]), ""),
        ("Loan repayments received", kes(s["repaid"]), ""),
        ("Purchases", kes(s["purchases"]), ""),
        ("Expenses", kes(s["expenses"]), ""),
        ("Net for the day (sales − purchases − expenses)", kes(s["net"]), ""),
    ]
    lines = [f"K-Ray Enterprise — {pretty}", ""]
    if s["empty"]:
        lines.append("Nothing was recorded today. If the shop traded, entries are missing.")
    for label, val, note in rows:
        lines.append(f"{label}: {val}" + (f"  ({note})" if note else ""))
    lines.append("")
    if s["money_in"] or s["money_out"]:
        lines.append("By account (money in / out today):")
        for k in ACCOUNTS:
            if k in s["money_in"] or k in s["money_out"]:
                lines.append(f"  {ACCOUNTS[k]}: in {kes(s['money_in'].get(k, 0))} / out {kes(s['money_out'].get(k, 0))}")
        lines.append("")
    if s["top"]:
        lines.append("Top sellers today:")
        for name, v in s["top"]:
            q = int(v["qty"]) if abs(v["qty"] - round(v["qty"])) < 1e-9 else round(v["qty"], 2)
            lines.append(f"  {name} — {q} sold, {kes(v['amount'])}")
        lines.append("")
    if s["pumice_any"]:
        p = s["pumice"]
        lines.append(f"Pumice today: sales {kes(p['sale'])}, purchases {kes(p['purchase'])}, expenses {kes(p['expense'])}, withdrawn {kes(p['withdrawal'])}")
        lines.append("")
    lines.append(f"Loans still owed by customers: {kes(s['loans_owed'])} ({s['loans_people']} customer(s))")
    text = "\n".join(lines)

    # ---- HTML version ----
    def tr(label, val, bold=False, color=None):
        style = "font-weight:700;" if bold else ""
        c = f"color:{color};" if color else ""
        return (f'<tr><td style="padding:6px 0;{style}">{escape(label.strip())}</td>'
                f'<td style="padding:6px 0;text-align:right;{style}{c}">{escape(val)}</td></tr>')
    net_color = "#16a34a" if s["net"] >= 0 else "#dc2626"
    h = [f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:auto;color:#111;">',
         f'<h2 style="margin:0 0 4px;">K-Ray Enterprise</h2><div style="color:#666;margin-bottom:16px;">{escape(pretty)}</div>']
    if s["empty"]:
        h.append('<p style="background:#fff7e6;border:1px solid #f5b942;padding:10px;border-radius:8px;">Nothing was recorded today. If the shop traded, entries are missing.</p>')
    h.append('<table style="width:100%;border-collapse:collapse;">')
    h.append(tr("Sales", kes(s["total_sales"]), True, "#16a34a"))
    h.append(tr("   paid now", kes(s["cash_sales"])))
    h.append(tr("   on credit (debt)", kes(s["credit_goods"])))
    h.append(tr("Loan repayments received", kes(s["repaid"])))
    h.append(tr("Purchases", kes(s["purchases"])))
    h.append(tr("Expenses", kes(s["expenses"])))
    h.append(tr("Net for the day", kes(s["net"]), True, net_color))
    h.append('</table>')
    if s["money_in"] or s["money_out"]:
        h.append('<h3 style="margin:20px 0 6px;">By account</h3><table style="width:100%;border-collapse:collapse;font-size:14px;">'
                 '<tr style="color:#666;"><td></td><td style="text-align:right;">In</td><td style="text-align:right;">Out</td></tr>')
        for k in ACCOUNTS:
            if k in s["money_in"] or k in s["money_out"]:
                h.append(f'<tr><td style="padding:4px 0;">{escape(ACCOUNTS[k])}</td>'
                         f'<td style="text-align:right;">{escape(kes(s["money_in"].get(k, 0)))}</td>'
                         f'<td style="text-align:right;">{escape(kes(s["money_out"].get(k, 0)))}</td></tr>')
        h.append('</table>')
    if s["top"]:
        h.append('<h3 style="margin:20px 0 6px;">Top sellers</h3><table style="width:100%;border-collapse:collapse;font-size:14px;">')
        for name, v in s["top"]:
            q = int(v["qty"]) if abs(v["qty"] - round(v["qty"])) < 1e-9 else round(v["qty"], 2)
            h.append(f'<tr><td style="padding:4px 0;">{escape(name)} <span style="color:#666;">×{q}</span></td><td style="text-align:right;">{escape(kes(v["amount"]))}</td></tr>')
        h.append('</table>')
    if s["pumice_any"]:
        p = s["pumice"]
        h.append(f'<h3 style="margin:20px 0 6px;">Pumice</h3><div style="font-size:14px;">Sales {escape(kes(p["sale"]))} · Purchases {escape(kes(p["purchase"]))} · Expenses {escape(kes(p["expense"]))} · Withdrawn {escape(kes(p["withdrawal"]))}</div>')
    h.append(f'<p style="margin-top:20px;padding:10px;background:#f3f4f6;border-radius:8px;font-size:14px;">Loans still owed by customers: <b>{escape(kes(s["loans_owed"]))}</b> ({s["loans_people"]} customer(s))</p></div>')
    return subject, text, "".join(h)


def _recipients():
    if OVERRIDE_TO:
        return OVERRIDE_TO
    with db_cursor() as cur:
        cur.execute("SELECT email FROM users WHERE email_verified = TRUE ORDER BY id")
        return [r["email"] for r in cur.fetchall()]


def send_summary_to(emails, day):
    s = build_summary(day)
    subject, text, html = render(s)
    ok = 0
    for e in emails:
        if mailer.send_email(e, subject, text, html):
            ok += 1
    return ok, len(emails)


def maybe_send_daily_summary():
    """Called from the health check. Cheap clock check first, so the database is only touched
    after the summary hour and only until today's summary has gone out."""
    global _sent_day, _last_attempt
    if not ENABLED or mailer.DEMO_MODE:
        return
    now = nairobi_now()
    day = now.strftime("%Y-%m-%d")
    if now.hour < SUMMARY_HOUR or _sent_day == day:
        return
    if time.time() - _last_attempt < 1800:       # retry at most every 30 min after a failure
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        _last_attempt = time.time()
        with db_cursor(commit=True) as cur:      # claim the day; only one worker wins
            cur.execute("INSERT INTO daily_summary_log (day) VALUES (%s) ON CONFLICT DO NOTHING RETURNING day", (day,))
            claimed = cur.fetchone() is not None
        if not claimed:
            _sent_day = day
            return
        emails = _recipients()
        ok, total = send_summary_to(emails, day) if emails else (0, 0)
        if total and ok == 0:                    # nothing went out: release the claim so it retries
            with db_cursor(commit=True) as cur:
                cur.execute("DELETE FROM daily_summary_log WHERE day = %s", (day,))
            print("[SUMMARY] sending failed for all recipients; will retry")
        else:
            with db_cursor(commit=True) as cur:
                cur.execute("UPDATE daily_summary_log SET recipients = %s WHERE day = %s", (ok, day))
            _sent_day = day
            print(f"[SUMMARY] daily summary for {day} sent to {ok}/{total}")
    except Exception as e:
        print(f"[SUMMARY ERROR] {e}")
    finally:
        _lock.release()


def trigger_in_background():
    """Non-blocking hook for the health route."""
    now = nairobi_now()
    if not ENABLED or mailer.DEMO_MODE or now.hour < SUMMARY_HOUR or _sent_day == now.strftime("%Y-%m-%d"):
        return
    threading.Thread(target=maybe_send_daily_summary, daemon=True).start()


@bp.post("/summary/send-now")
@login_required
def send_now():
    """Email today's summary to the logged-in user only (a test button)."""
    if mailer.DEMO_MODE:
        return jsonify({"error": "Email is not set up on the server yet."}), 400
    with db_cursor() as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (g.user_id,))
        row = cur.fetchone()
    day = nairobi_now().strftime("%Y-%m-%d")
    ok, _ = send_summary_to([row["email"]], day)
    if not ok:
        return jsonify({"error": "Could not send: " + (mailer.last_error() or "unknown error")}), 502
    return jsonify({"sent_to": row["email"], "day": day})
