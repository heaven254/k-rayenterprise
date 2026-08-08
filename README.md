# K-Ray Enterprise — Python Backend

A Flask + PostgreSQL REST API that mirrors the K-Ray Enterprise HTML app's
data model, and enforces the **User vs Admin** permission split server-side
(the HTML prototype only enforced it in the browser, which anyone could
bypass with dev tools — this backend makes it real).

- **User role**: full read/write access to their own business's records —
  can add and edit, but **cannot delete**.
- **Admin role**: read access to *every* business's records, and can
  **delete anything** — but cannot create or edit anything. Reaching admin
  requires a normal password login *plus* a one-time 6-digit email code.

Uses PostgreSQL for real persistence — data survives restarts and
redeploys (unlike SQLite on most free hosts, where the disk resets).

## 1. Deploying on Render (recommended path)

1. **Create a Postgres database**: Render dashboard → **New +** → **PostgreSQL**.
   Pick the free tier. Wait for it to finish provisioning.
2. **Create this web service**: **New +** → **Web Service**, connect your repo.
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn "app:create_app()"`
3. **Connect the database to this service**: in the web service's
   **Environment** tab, add an environment variable `DATABASE_URL` and set
   its value to your Postgres database's **Internal Database URL** (copy it
   from the Postgres database's page on Render — under "Connections").
   Render's docs also let you link them automatically when creating the
   web service, which sets `DATABASE_URL` for you.
4. Deploy. On startup, `init_db()` creates all tables automatically if
   they don't exist yet — no manual migration step needed.

Because the database is now a separate managed service instead of a file
on the app's own disk, your data will **survive service restarts, idle
spin-downs, and redeploys** — the three things that were wiping your data
before.

## 2. Local setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then set KRAY_DATABASE_URL to a local/remote Postgres URL
```

## 3. Run it

```bash
python app.py
```

The API starts on `http://localhost:5000`. Tables are created automatically
on first run against whatever `DATABASE_URL` / `KRAY_DATABASE_URL` points to.

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

## 4. Run the test suite

A self-contained functional test (no server process needed — it drives
the app through Flask's test client) exercises signup, login, admin
verification, and the full read/write/delete permission matrix. It needs
a real (disposable/test) Postgres database to run against:

```bash
export KRAY_DATABASE_URL=postgresql://user:pass@localhost:5432/kray_test
python test_flow.py
```

## 5. API Reference

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

## 6. Connecting the HTML frontend

The `abila.html` frontend already has the sync layer built in. On the
login screen, click **⚙ Configure** and enter this backend's URL (e.g.
your Render web service URL). Once connected, sign-up/login/admin
verification and every data module (Sales, Purchases, Credit Sales,
Expenses, Cash, Transfers, Pumice, Products, Stock Logs, Comments) read
from and write to this API instead of the browser's local storage, so
the same account shows the same data from any device.

## 7. Project layout

```
backend/
├── app.py               # Flask app factory, CORS, error handlers
├── db.py                 # Postgres schema + connection helpers
├── auth.py               # Password hashing, JWT, role decorators
├── mailer.py              # Admin code email sender (+ demo-mode fallback)
├── routes_auth.py         # /api/auth/* endpoints
├── routes_resources.py    # /api/products, /api/sales, etc.
├── test_flow.py           # End-to-end functional test (needs a Postgres DB)
├── requirements.txt
├── .env.example
└── README.md
```
