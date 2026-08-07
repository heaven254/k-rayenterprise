"""
Quick end-to-end smoke test using Flask's test client (no server needed).
Run with: python test_flow.py
"""
import os
import sys
import json

# Use an isolated test DB so this doesn't touch a real kray.db
os.environ["KRAY_DB_PATH"] = os.path.join(os.path.dirname(__file__), "test_kray.db")
if os.path.exists(os.environ["KRAY_DB_PATH"]):
    os.remove(os.environ["KRAY_DB_PATH"])

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


# 1. Signup
code, body = call("post", "/api/auth/signup", json={
    "name": "Amina Traders", "business": "Amina Wholesale", "email": "amina@example.com", "password": "secret123"
})
expect(code == 201, f"signup succeeds (got {code}: {body})")
user_token = body["token"]
expect(body["role"] == "user", "signup issues 'user' role token")

# 2. Duplicate signup rejected
code, body = call("post", "/api/auth/signup", json={
    "name": "X", "email": "amina@example.com", "password": "x"
})
expect(code == 409, "duplicate signup rejected")

# 3. Login as user
code, body = call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "secret123", "role": "user"})
expect(code == 200 and body.get("role") == "user", "user login works")
user_token = body["token"]

# 4. Wrong password
code, body = call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "wrong", "role": "user"})
expect(code == 401, "wrong password rejected")

# 5. Admin login requires verification
code, body = call("post", "/api/auth/login", json={"email": "amina@example.com", "password": "secret123", "role": "admin"})
expect(code == 200 and body.get("requires_verification") is True, "admin login requires verification")
expect("demo_code" in body, "demo mode surfaces the code (no SMTP configured in test env)")
verification_id = body["verification_id"]
demo_code = body["demo_code"]

# 6. Wrong code rejected
code, body = call("post", "/api/auth/verify-admin", json={"verification_id": verification_id, "code": "000000"})
expect(code == 401, "wrong admin code rejected")

# 7. Correct code issues admin token
code, body = call("post", "/api/auth/verify-admin", json={"verification_id": verification_id, "code": demo_code})
expect(code == 200 and body.get("role") == "admin", "correct admin code issues admin token")
admin_token = body["token"]

# 8. Reused code rejected
code, body = call("post", "/api/auth/verify-admin", json={"verification_id": verification_id, "code": demo_code})
expect(code == 400, "reused admin code rejected")

# 9. /me works for both roles
code, body = call("get", "/api/auth/me", token=user_token)
expect(code == 200 and body["role"] == "user", "/me reflects user role")
code, body = call("get", "/api/auth/me", token=admin_token)
expect(code == 200 and body["role"] == "admin", "/me reflects admin role")

# 10. User can create a product; admin cannot
code, body = call("post", "/api/products", token=user_token, json={"name": "Rice 25kg", "category": "Grains", "cost": 2000, "price": 2500})
expect(code == 201, f"user can create product (got {code}: {body})")
product_id = body["id"]

code, body = call("post", "/api/products", token=admin_token, json={"name": "Should Fail", "cost": 1, "price": 2})
expect(code == 403, "admin CANNOT create a product")

# 11. User can update price; admin cannot
code, body = call("put", f"/api/products/{product_id}", token=user_token, json={"field": "price", "value": 2600})
expect(code == 200 and body["price"] == 2600, "user can update product price")

code, body = call("put", f"/api/products/{product_id}", token=admin_token, json={"field": "price", "value": 1})
expect(code == 403, "admin CANNOT update product price")

# 12. User can NOT delete; admin CAN delete
code, body = call("delete", f"/api/products/{product_id}", token=user_token)
expect(code == 403, "user CANNOT delete a product")

code, body = call("delete", f"/api/products/{product_id}", token=admin_token)
expect(code == 200 and body["deleted"] == product_id, "admin CAN delete a product")

# 13. No token at all -> 401
code, body = call("get", "/api/products")
expect(code == 401, "unauthenticated request rejected")

# 14. Sales + receipt grouping + credit sales with payments
code, body = call("post", "/api/sales", token=user_token, json={
    "receipt_id": 555, "date": "2026-08-07", "item": "Sugar 2kg", "customer": "Walk-in",
    "account": "cash", "qty": 3, "price": 250
})
expect(code == 201, "user can record a sale")

code, body = call("post", "/api/credit-sales", token=user_token, json={
    "date": "2026-08-07", "customer": "John K.", "item": "Cooking Oil 5L", "qty": 2, "price": 1200
})
expect(code == 201 and body["total"] == 2400 and body["remaining"] == 2400, "credit sale totals computed correctly")
credit_id = body["id"]

code, body = call("post", f"/api/credit-sales/{credit_id}/payments", token=user_token, json={"amount": 1000, "account": "mpesa", "date": "2026-08-08"})
expect(code == 201 and body["paid"] == 1000 and body["remaining"] == 1400, "credit payment updates paid/remaining")

code, body = call("delete", f"/api/credit-sales/{credit_id}", token=user_token)
expect(code == 403, "user cannot delete credit sale")
code, body = call("delete", f"/api/credit-sales/{credit_id}", token=admin_token)
expect(code == 200, "admin can delete credit sale")

# 15. Admin sees data across all users (oversight); user sees only their own
code, body = call("post", "/api/auth/signup", json={"name": "Second Biz", "email": "second@example.com", "password": "pw123456"})
second_token = body["token"]
call("post", "/api/sales", token=second_token, json={"date": "2026-08-07", "item": "Salt 1kg", "account": "cash", "qty": 1, "price": 100})

code, body = call("get", "/api/sales", token=user_token)
expect(all(s.get("item") != "Salt 1kg" for s in body), "user only sees their OWN sales")

code, body = call("get", "/api/sales", token=admin_token)
expect(any(s.get("item") == "Salt 1kg" for s in body), "admin sees ALL businesses' sales")

print("\nAll checks passed.")
