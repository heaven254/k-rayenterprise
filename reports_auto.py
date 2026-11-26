"""
reports_auto.py — automatic weekly and monthly report emails (owner only).

Runs with no extra setup, like the daily summary: the uptime pinger visits /api/health,
and the first visit after 08:00 Nairobi time
  * on a Monday      emails last week's report (Mon-Sun)
  * on the 1st       emails last month's report
Each is sent once (a row in daily_summary_log such as "week:2026-11-16" stops repeats).
Owner can switch this off in Team > Safety settings (setting auto_reports) or with
KRAY_AUTO_REPORTS=off. The report page in the app builds the same numbers by hand.
"""
import os
import datetime
import threading
from html import escape
from db import db_cursor
from auth import owner_ids, get_setting
import mailer

EAT = datetime.timedelta(hours=3)
ENV_ON = os.environ.get("KRAY_AUTO_REPORTS", "on").strip().lower() != "off"
SEND_HOUR = int(os.environ.get("KRAY_REPORT_HOUR", "8") or 8)
_lock = threading.Lock()
_checked = None


def _now():
    return datetime.datetime.utcnow() + EAT


def kes(n):
    n = float(n or 0)
    return "KSh {:,.0f}".format(n) if abs(n - round(n)) < 0.005 else "KSh {:,.2f}".format(n)


def _sum(rows, f):
    return sum(float(f(r) or 0) for r in rows)


def build_report(frm, to):
    d = lambda r: str(r["date"] or "")[:10]
    inr = lambda r: frm <= d(r) <= to
    with db_cursor() as cur:
        cur.execute("SELECT * FROM sales"); sales_all = cur.fetchall()
        cur.execute("SELECT * FROM purchases"); purch_all = cur.fetchall()
        cur.execute("SELECT id, date, name, category, amount, account, item FROM expenses"); exp_all = cur.fetchall()
        cur.execute("SELECT * FROM cash"); cash_all = cur.fetchall()
        cur.execute("SELECT * FROM transfers"); tr_all = cur.fetchall()
        cur.execute("SELECT * FROM pumice"); pm_all = cur.fetchall()
        cur.execute("SELECT * FROM credit_sales"); cs_all = cur.fetchall()
        cur.execute("SELECT * FROM credit_payments"); cp_all = cur.fetchall()
    sales = [r for r in sales_all if inr(r)]
    purch = [r for r in purch_all if inr(r)]
    exps = [r for r in exp_all if inr(r)]
    tot_sales = _sum(sales, lambda r: r["qty"] * r["price"])
    tot_purch = _sum(purch, lambda r: r["qty"] * r["cost"])
    tot_exp = _sum(exps, lambda r: r["amount"])
    is_ch = lambda s: "pesa charge" in s.lower().replace("-", "")
    charges = _sum([e for e in exps if is_ch((e["category"] or "") + " " + (e["name"] or ""))], lambda r: r["amount"])
    given = [c for c in cs_all if inr(c)]
    repaid = _sum([p for p in cp_all if inr(p)], lambda p: p["amount"])
    owed_now = _sum(cs_all, lambda c: c["remaining"])
    owed_by = {}
    for c in cs_all:
        if (c["remaining"] or 0) > 0:
            k = c["customer"] or "Unknown"; owed_by[k] = owed_by.get(k, 0) + c["remaining"]
    top_owed = sorted(owed_by.items(), key=lambda kv: -kv[1])[:5]
    by_cat = {}
    for e in exps:
        k = e["category"] or "Other"; by_cat[k] = by_cat.get(k, 0) + (e["amount"] or 0)
    top_cat = sorted(by_cat.items(), key=lambda kv: -kv[1])[:6]
    by_item = {}
    for s in sales:
        by_item[s["item"]] = by_item.get(s["item"], 0) + s["qty"] * s["price"]
    top_items = sorted(by_item.items(), key=lambda kv: -kv[1])[:5]

    # Pumice
    wo = lambda p: p["type"] == "expense" and bool(p["item"])
    pm = [p for p in pm_all if inr(p)]
    pm_sales = _sum([p for p in pm if p["type"] == "sale"], lambda p: p["amount"])
    pm_exp = _sum([p for p in pm if p["type"] == "expense" and not wo(p)], lambda p: p["amount"]) + \
        _sum([p for p in pm if p["type"] == "purchase"], lambda p: p["amount"])
    pm_ch = _sum([p for p in pm if p["type"] == "expense" and is_ch(p["item_desc"] or "")], lambda p: p["amount"])
    pm_paid = _sum([p for p in pm if p["type"] == "withdrawal"], lambda p: p["amount"])
    settle = lambda p: p["type"] == "withdrawal" and str(p["item_desc"] or "").startswith("✅ Settlement")
    last = max([p["id"] for p in pm_all if settle(p)] or [0])
    cur_b = [p for p in pm_all if p["id"] > last]
    pm_owed = (_sum([p for p in cur_b if p["type"] == "sale"], lambda p: p["amount"])
               - _sum([p for p in cur_b if p["type"] == "purchase" and not wo(p)], lambda p: p["amount"])
               - _sum([p for p in cur_b if p["type"] == "expense"], lambda p: p["amount"])
               - _sum([p for p in cur_b if p["type"] == "withdrawal" and not settle(p)], lambda p: p["amount"]))

    # balances now
    bal = {"pochi": 0.0, "mpesa": 0.0, "cash": 0.0, "moneybox": 0.0}
    def add(k, v): bal[k if k in bal else "cash"] += v
    for r in sales_all: add(r["account"], r["qty"] * r["price"])
    for r in cp_all: add(r["account"], r["amount"] or 0)
    for r in purch_all: add(r["account"], -(r["qty"] * r["cost"]))
    for r in exp_all:
        if not (r["item"] and r["account"] == "writeoff"): add(r["account"], -(r["amount"] or 0))
    for r in cash_all: add(r["account"], r["amount"] or 0)
    for r in tr_all:
        m = r["amount"] or 0
        if r["from_account"] in bal: bal[r["from_account"]] -= m
        if r["to_account"] in bal: bal[r["to_account"]] += m
    for p in pm_all:
        if wo(p): continue
        if p["type"] == "sale":
            amt = p["amount"] or 0
            cashp = amt if p["account"] == "cash" else (min(amt, max(0, p["cash_part"] or 0)) if p["account"] == "split" else 0)
            bal["cash"] += cashp; bal["mpesa"] += amt - cashp
        else:
            bal["mpesa"] -= p["amount"] or 0
    return dict(frm=frm, to=to, tot_sales=tot_sales, tot_purch=tot_purch, tot_exp=tot_exp, charges=charges,
                net=tot_sales - tot_purch - tot_exp, n_given=len(given), given=_sum(given, lambda c: c["total"]),
                repaid=repaid, owed_now=owed_now, top_owed=top_owed, top_cat=top_cat, top_items=top_items,
                pm_sales=pm_sales, pm_exp=pm_exp, pm_ch=pm_ch, pm_paid=pm_paid, pm_owed=pm_owed, bal=bal,
                n_sales=len(sales))


def render(r, biz="K-Ray Enterprise"):
    th = "text-align:left;padding:6px 8px;background:#f3f4f6;font-size:12px;text-transform:uppercase;color:#374151;"
    td = "padding:7px 8px;border-bottom:1px solid #e5e7eb;font-size:14px;color:#111827;"
    tr = td + "text-align:right;font-weight:600;white-space:nowrap;"
    row = lambda a, b, bold=False: f'<tr><td style="{td}{"font-weight:700;" if bold else ""}">{a}</td><td style="{tr}">{b}</td></tr>'
    head = lambda t: f'<tr><th colspan="2" style="{th}">{t}</th></tr>'
    lst = lambda a: "".join(row(escape(str(k)), kes(v)) for k, v in a) or row("<i>none</i>", "")
    col = "#047857" if r["net"] >= 0 else "#b91c1c"
    b = r["bal"]
    html = f'''<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;padding:12px;color:#111827;">
<div style="text-align:center;margin-bottom:12px;"><div style="font-size:20px;font-weight:800;">{escape(biz)}</div>
<div style="font-size:13px;color:#4b5563;">Business report · {r["frm"]} to {r["to"]}</div></div>
<table style="width:100%;border-collapse:collapse;">
{head("Money in &amp; out")}{row(f'Sales ({r["n_sales"]} items)', kes(r["tot_sales"]))}{row("Purchases (stock bought)", kes(r["tot_purch"]))}
{row("Expenses", kes(r["tot_exp"]))}{row("&nbsp;&nbsp;of which M-Pesa charges", kes(r["charges"]))}
{row("Net profit", f'<span style="color:{col};">{kes(r["net"])}</span>', True)}
{head("Credit loans")}{row(f'Given on credit in this period ({r["n_given"]})', kes(r["given"]))}{row("Repayments received in this period", kes(r["repaid"]))}
{row("Total still owed to you now", kes(r["owed_now"]), True)}
{(head("Biggest debtors") + lst(r["top_owed"])) if r["top_owed"] else ""}
{head("Pumice (family stock you sell for others)")}{row("Pumice sales", kes(r["pm_sales"]))}{row("Pumice expenses &amp; purchases", kes(r["pm_exp"]))}
{row("&nbsp;&nbsp;of which M-Pesa charges", kes(r["pm_ch"]))}{row("Already paid out in this period", kes(r["pm_paid"]))}
{row("Owed to the Pumice owner now (current batch)", kes(r["pm_owed"]), True)}
{head("Where the money is now")}{row("Cash", kes(b["cash"]))}{row("M-Pesa", kes(b["mpesa"]))}{row("Pochi", kes(b["pochi"]))}{row("Money box", kes(b["moneybox"]))}
{head("Top selling items")}{lst(r["top_items"])}{head("Biggest expense groups")}{lst(r["top_cat"])}
</table><div style="text-align:center;font-size:11px;color:#6b7280;margin-top:12px;">Automatic report from your K-Ray app</div></div>'''
    text = (f"Business report {r['frm']} to {r['to']}\nSales {kes(r['tot_sales'])}, purchases {kes(r['tot_purch'])}, "
            f"expenses {kes(r['tot_exp'])} (M-Pesa charges {kes(r['charges'])}), net profit {kes(r['net'])}.\n"
            f"Credit owed {kes(r['owed_now'])}. Pumice owed to owner {kes(r['pm_owed'])}.")
    return html, text


def _send(frm, to, label):
    with db_cursor() as cur:
        ids = owner_ids(cur)
        if not ids:
            return 0
        cur.execute("SELECT email, business FROM users WHERE id = ANY(%s) AND disabled = FALSE", (list(ids),))
        owners = cur.fetchall()
    r = build_report(frm, to)
    ok = 0
    for o in owners:
        html, text = render(r, o["business"] or "K-Ray Enterprise")
        if mailer.send_email(o["email"], f"{label} report — {frm} to {to}", text, html=html):
            ok += 1
    return ok


def maybe_send_reports():
    global _checked
    if not ENV_ON or mailer.DEMO_MODE:
        return
    now = _now()
    today = now.strftime("%Y-%m-%d")
    if now.hour < SEND_HOUR or _checked == today:
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        if get_setting("auto_reports", "true") == "false":
            _checked = today
            return
        jobs = []
        if now.weekday() == 0:
            mon = (now - datetime.timedelta(days=7)).date()
            jobs.append(("week:" + mon.isoformat(), mon.isoformat(), (mon + datetime.timedelta(days=6)).isoformat(), "Weekly"))
        if now.day == 1:
            last = now.date().replace(day=1) - datetime.timedelta(days=1)
            first = last.replace(day=1)
            jobs.append(("month:" + first.strftime("%Y-%m"), first.isoformat(), last.isoformat(), "Monthly"))
        for key, frm, to, label in jobs:
            with db_cursor(commit=True) as cur:
                cur.execute("INSERT INTO daily_summary_log (day, recipients) VALUES (%s, 0) ON CONFLICT DO NOTHING RETURNING day", (key,))
                claimed = cur.fetchone() is not None
            if not claimed:
                continue
            ok = _send(frm, to, label)
            with db_cursor(commit=True) as cur:
                if ok == 0:
                    cur.execute("DELETE FROM daily_summary_log WHERE day = %s", (key,))   # retry on a later ping
                else:
                    cur.execute("UPDATE daily_summary_log SET recipients = %s WHERE day = %s", (ok, key))
            print(f"[REPORT] {label} report {frm}..{to} sent to {ok}")
            if ok == 0:
                return
        _checked = today
    except Exception as e:
        print(f"[REPORT ERROR] {e}")
    finally:
        _lock.release()


def trigger_in_background():
    now = _now()
    if ENV_ON and not mailer.DEMO_MODE and now.hour >= SEND_HOUR and _checked != now.strftime("%Y-%m-%d") \
            and (now.weekday() == 0 or now.day == 1):
        threading.Thread(target=maybe_send_reports, daemon=True).start()
