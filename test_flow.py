"""
Quick end-to-end smoke test using Flask's test client (no server needed).

Requires a real Postgres database — set DATABASE_URL (or KRAY_DATABASE_URL)
to a scratch/test database before running this, e.g.:

    export KRAY_DATABASE_URL=postgresql://user:pass@localhost:5432/kray_test
    python test_flow.py

This will create and use real tables in that database (via init_db()),
so always point it at a disposable test database, never production.

Run with: python test_flow.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from app import app  # noqa: E402

client = app.test_client()


def call(method, path, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = getattr(client, method)(path, headers=headers, **kwargs)
    return resp.status_code, (resp.get_json() if resp.data else None)


def expect(cond, msg):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {msg}")
    if not cond:
        raise SystemExit(1)


# 1. Signup requires email verification — no token yet
code, body = call("post", "/api/auth/signup", json={
    "name": "Amina Traders", "business": "K-Ray Enterprise", "email": "amina@example.com", "password": "secret123"
})
expect(code == 201 and body.get("requires_verification") is True, f"signup requires verification (got {code}: {body})")
expect("demo_code" in body, "demo mode surfaces the signup code (no SMTP configured in test env)")
signup_verification_id = body["verification_id"]
signup_demo_code = body["demo_code"]

# 2. Duplicate signup rejected
code, body = call("post", "/api/auth/signup", json={
    "name": "X", "email": "amina@example.com", "password": "x"
})
expect(code == 409, "duplicate signup rejected")

# 3. Logging in before verifying email re-sends a signup code instead of a token
code, body = call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "secret123"})
expect(code == 200 and body.get("requires_verification") is True and body.get("reason") == "email_not_verified",
       f"login before verification re-sends signup code (got {code}: {body})")

# 4. Wrong code rejected
code, body = call("post", "/api/auth/verify-code", json={"verification_id": signup_verification_id, "code": "000000"})
expect(code == 401, "wrong signup code rejected")

# 5. Correct code verifies email and issues a token
code, body = call("post", "/api/auth/verify-code", json={"verification_id": signup_verification_id, "code": signup_demo_code})
expect(code == 200 and body["user"]["emailVerified"] is True and "token" in body,
       f"correct signup code verifies email + issues token (got {code}: {body})")
amina_token = body["token"]

# 6. Reused code rejected
code, body = call("post", "/api/auth/verify-code", json={"verification_id": signup_verification_id, "code": signup_demo_code})
expect(code == 400, "reused signup code rejected")

# 7. Now that email is verified, normal login works directly
code, body = call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "secret123"})
expect(code == 200 and "token" in body, "login works after email verified")
amina_token = body["token"]

# 8. Wrong password
code, body = call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "wrong"})
expect(code == 401, "wrong password rejected")

# 9. /me works
code, body = call("get", "/api/auth/me", token=amina_token)
expect(code == 200 and body["user"]["email"] == "amina@example.com", "/me returns the logged-in user")

# 10. No token at all -> 401
code, body = call("get", "/api/products")
expect(code == 401, "unauthenticated request rejected")

# 11. Second teammate signs up + verifies — same shared business
code, body = call("post", "/api/auth/signup", json={"name": "John (Staff)", "email": "john@example.com", "password": "pw123456"})
john_verification_id = body["verification_id"]
john_code = body["demo_code"]
code, body = call("post", "/api/auth/verify-code", json={"verification_id": john_verification_id, "code": john_code})
john_token = body["token"]

# 12. Everyone can create AND delete — no admin/user split anymore
code, body = call("post", "/api/products", token=amina_token, json={"name": "Rice 25kg", "category": "Grains", "cost": 2000, "price": 2500})
expect(code == 201, f"any logged-in user can create a product (got {code}: {body})")
product_id = body["id"]

code, body = call("put", f"/api/products/{product_id}", token=john_token, json={"field": "price", "value": 2600})
expect(code == 200 and body["price"] == 2600, "a DIFFERENT teammate can edit that same product (shared business)")

code, body = call("delete", f"/api/products/{product_id}", token=john_token)
expect(code == 200 and body["deleted"] == product_id, "any logged-in user can delete — no admin role required")

# 13. Records created by one teammate are visible to the other (shared data, not per-account)
code, body = call("post", "/api/sales", token=amina_token, json={
    "receipt_id": 555, "date": "2026-08-07", "item": "Sugar 2kg", "customer": "Walk-in",
    "account": "cash", "qty": 3, "price": 250
})
expect(code == 201, "amina can record a sale")

code, body = call("get", "/api/sales", token=john_token)
expect(any(s.get("item") == "Sugar 2kg" for s in body), "john sees the SAME shared sale amina just created")

# 14. Credit sales + payments, shared visibility
code, body = call("post", "/api/credit-sales", token=amina_token, json={
    "date": "2026-08-07", "customer": "John K.", "item": "Cooking Oil 5L", "qty": 2, "price": 1200
})
expect(code == 201 and body["total"] == 2400 and body["remaining"] == 2400, "credit sale totals computed correctly")
credit_id = body["id"]

code, body = call("post", f"/api/credit-sales/{credit_id}/payments", token=john_token, json={"amount": 1000, "account": "mpesa", "date": "2026-08-08"})
expect(code == 201 and body["paid"] == 1000 and body["remaining"] == 1400,
       "a different teammate can record a payment on the same credit sale")

code, body = call("delete", f"/api/credit-sales/{credit_id}", token=amina_token)
expect(code == 200, "any teammate can delete a credit sale")

# 15. Corrected schemas: purchases (cost+category), expenses (name), cash (source), pumice (desc, 3 types)
code, body = call("post", "/api/purchases", token=amina_token, json={
    "date": "2026-08-07", "item": "Maize Flour", "category": "Grains",
    "supplier": "ABC Millers", "account": "mpesa", "qty": 10, "cost": 120
})
expect(code == 201 and body["cost"] == 120 and body["category"] == "Grains", f"purchases use cost+category (got {code}: {body})")

code, body = call("post", "/api/expenses", token=amina_token, json={"date": "2026-08-07", "name": "Rent", "category": "Overheads", "amount": 5000, "account": "cash"})
expect(code == 201 and body["name"] == "Rent", "expenses use 'name' field")

code, body = call("post", "/api/cash", token=amina_token, json={"date": "2026-08-07", "source": "Owner Injection", "account": "cash", "amount": 10000})
expect(code == 201 and body["source"] == "Owner Injection", "cash has 'source' field")

code, body = call("post", "/api/pumice", token=amina_token, json={"date": "2026-08-07", "type": "purchase", "desc": "Pumice stone batch", "qty": 5, "amount": 2000})
expect(code == 201 and body["type"] == "purchase", "pumice accepts 'purchase' type (3-way enum)")

code, body = call("post", "/api/stock-logs", token=amina_token, json={"date": "2026-08-07", "type": "add", "item": "Rice 25kg", "qty": 20, "cost": 2000, "comment": "Restock"})
expect(code == 201 and body["item"] == "Rice 25kg", "stock-logs use item name directly")

# 16. Editing (PUT) on a generic resource
code, body = call("post", "/api/expenses", token=amina_token, json={"date": "2026-08-07", "name": "Fuel", "category": "Transport", "amount": 800, "account": "cash"})
expense_id = body["id"]
code, body = call("put", f"/api/expenses/{expense_id}", token=john_token, json={"amount": 950})
expect(code == 200 and body["amount"] == 950, f"any teammate can edit an expense (got {code}: {body})")

code, body = call("put", f"/api/expenses/{expense_id}", token=john_token, json={})
expect(code == 400, "editing with no fields is rejected")

code, body = call("put", "/api/expenses/999999", token=john_token, json={"amount": 1})
expect(code == 404, "editing a non-existent record 404s")

# 17. Product edit now supports name/category, not just price/cost
code, body = call("post", "/api/products", token=amina_token, json={"name": "Beans 2kg", "category": "Legumes", "cost": 300, "price": 400})
prod2_id = body["id"]
code, body = call("put", f"/api/products/{prod2_id}", token=john_token, json={"name": "Beans 2kg (Local)", "category": "Grains"})
expect(code == 200 and body["name"] == "Beans 2kg (Local)" and body["category"] == "Grains",
       f"product name/category are editable, not just price (got {code}: {body})")

# 18. Activity log records create/update/delete
code, body = call("get", "/api/activity", token=amina_token)
expect(code == 200 and len(body) > 0, "activity log returns entries")
expect(any(a["resource_type"] == "expenses" and a["action"] == "updated" for a in body),
       "activity log recorded the expense edit")
expect(any(a["resource_type"] == "products" and a["action"] == "created" for a in body),
       "activity log recorded product creation")
expect(any(a["user_name"] in ("Amina Traders", "John (Staff)") for a in body),
       "activity log entries are attributed to the acting user")

# 19. Rate limiting kicks in after repeated failed logins
for _ in range(6):
    call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "wrong-password"})
code, body = call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "wrong-password"})
expect(code == 429, f"rate limiting blocks after repeated failed attempts (got {code}: {body})")

# 20. Real password reset flow
code, body = call("post", "/api/auth/forgot-password", json={"email": "john@example.com"})
expect(code == 200 and body.get("requires_verification") is True, f"forgot-password issues a code (got {code}: {body})")
reset_verification_id = body["verification_id"]
reset_code = body["demo_code"]

code, body = call("post", "/api/auth/reset-password", json={
    "verification_id": reset_verification_id, "code": "000000", "new_password": "newpass123"
})
expect(code == 401, "wrong reset code rejected")

code, body = call("post", "/api/auth/reset-password", json={
    "verification_id": reset_verification_id, "code": reset_code, "new_password": "newpass123"
})
expect(code == 200 and "token" in body, f"correct reset code changes password + logs in (got {code}: {body})")

code, body = call("post", "/api/auth/login", json={"email": "john@example.com", "password": "newpass123"})
expect(code == 200 and "token" in body, "can log in with the NEW password after reset")

code, body = call("post", "/api/auth/login", json={"email": "john@example.com", "password": "pw123456"})
expect(code == 401, "OLD password no longer works after reset")

# 21. forgot-password on a non-existent email doesn't leak account existence
code, body = call("post", "/api/auth/forgot-password", json={"email": "nobody-here@example.com"})
expect(code == 200 and "demo_code" not in body, "forgot-password gives the same response for unknown emails (no enumeration)")

print("\nAll checks passed.")
