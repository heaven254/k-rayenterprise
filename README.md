# K-Ray Enterprise — Python Backend

A Flask + SQLite REST API that mirrors the K-Ray Enterprise HTML app's data
model, and enforces the **User vs Admin** permission split server-side
(the HTML prototype only enforced it in the browser, which anyone could
bypass with dev tools — this backend makes it real).

- **User role**: full read/write access to their own business's records —
  can add and edit, but **cannot delete**.
- **Admin role**: read access to *every* business's records, and can
  **delete anything** — but cannot create or edit anything. Reaching admin
  requires a normal password login *plus* a one-time 6-digit email code.

No database server or ORM required — it uses Python's built-in `sqlite3`
module, so setup is just Flask + PyJWT.

## 1. Setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then edit values as needed
```

## 2. Run it

```bash
python app.py
```

The API starts on `http://localhost:5000`. A SQLite file `kray.db` is
created automatically on first run (change its location with
`KRAY_DB_PATH`).

For production, run behind gunicorn instead of the Flask dev server:

```bash
gunicorn "app:create_app()" -w 4 -b 0.0.0.0:5000
```

### Admin email codes — demo mode

If you don't set `KRAY_SMTP_*` environment variables, the backend runs in
**demo mode**: verification codes are printed to the server console *and*
returned directly in the `/api/auth/login` response body as `demo_code`,
so you can test the whole admin flow without any email setup. Set the SMTP
variables in `.env` to send real emails instead (see `.env.example`).

## 3. Run the test suite

A self-contained functional test (no server process needed — it drives
the app through Flask's test client) exercises signup, login, admin
verification, and the full read/write/delete permission matrix:

```bash
python test_flow.py
```

## 4. API Reference

All request/response bodies are JSON. Authenticated endpoints expect:

```
Authorization: Bearer <token>
```

### Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/signup` | — | `{name, business, email, password}` → creates a user, returns a **user**-role token |
| POST | `/api/auth/login` | — | `{email, password, role}` (`role` is `"user"` or `"admin"`). `role:"user"` returns a token immediately. `role:"admin"` returns `{requires_verification:true, verification_id, demo_code?}` instead. |
| POST | `/api/auth/verify-admin` | — | `{verification_id, code}` → returns an **admin**-role token once the code matches |
| POST | `/api/auth/resend-admin-code` | — | `{verification_id}` → issues a fresh code for the same pending login |
| GET | `/api/auth/me` | any | Returns the current user + role |

### Business records

Every resource below follows the same shape:

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/<resource>` | any logged-in user | Users see only their own records; admins see every business's records |
| POST | `/api/<resource>` | **user** only | 403 if called as admin |
| DELETE | `/api/<resource>/<id>` | **admin** only | 403 if called as user |

Resources: `products`, `purchases`, `sales`, `credit-sales`, `expenses`,
`cash`, `transfers`, `pumice`, `stock-logs`, `comments`.

Extra endpoints:

| Method | Path | Who | Description |
|---|---|---|---|
| PUT | `/api/products/<id>` | user | `{field: "cost"|"price", value}` — inline price editing |
| POST | `/api/credit-sales/<id>/payments` | user | `{amount, account, date}` — records a repayment and recalculates `paid`/`remaining` on the parent credit sale |

### Example: full login → action flow

```bash
# 1. Sign up
curl -X POST localhost:5000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"name":"Amina","business":"Amina Wholesale","email":"amina@example.com","password":"secret123"}'

# 2. Log in as a normal user, add a product
TOKEN=$(curl -s -X POST localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"amina@example.com","password":"secret123","role":"user"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -X POST localhost:5000/api/products \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Rice 25kg","category":"Grains","cost":2000,"price":2500}'

# 3. Log in as admin (step 1 of 2 — get a verification code)
curl -X POST localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"amina@example.com","password":"secret123","role":"admin"}'
# -> {"requires_verification": true, "verification_id": 1, "demo_code": "482913", ...}

# 4. Verify the code to get an admin token, then delete the product
ADMIN_TOKEN=$(curl -s -X POST localhost:5000/api/auth/verify-admin \
  -H "Content-Type: application/json" \
  -d '{"verification_id":1,"code":"482913"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -X DELETE localhost:5000/api/products/1 -H "Authorization: Bearer $ADMIN_TOKEN"
```

## 5. Connecting the existing HTML frontend

The `abila.html` prototype currently stores everything in `localStorage`
and checks the admin role only in JavaScript. To wire it up to this
backend, the frontend's state functions (`saveStateToStorage`,
`loadStateFromStorage`, `handleLogin`, `handleAdminVerify`, the various
`add*`/`delete*` functions) would need to call these endpoints with
`fetch()` instead of reading/writing `localStorage` directly. Happy to do
that wiring as a follow-up if you'd like the two connected end-to-end.

## 6. Project layout

```
backend/
├── app.py               # Flask app factory, CORS, error handlers
├── db.py                 # SQLite schema + connection helpers
├── auth.py               # Password hashing, JWT, role decorators
├── mailer.py              # Admin code email sender (+ demo-mode fallback)
├── routes_auth.py         # /api/auth/* endpoints
├── routes_resources.py    # /api/products, /api/sales, etc.
├── test_flow.py           # End-to-end functional test
├── requirements.txt
├── .env.example
└── README.md
```
