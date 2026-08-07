# K-Ray Enterprise — Render PostgreSQL setup

1. Create a Render PostgreSQL database.
2. In the `krayenterprise` Web Service, open Environment.
3. Add:
   - Key: `DATABASE_URL`
   - Value: the PostgreSQL **Internal Database URL** from the Render database.
4. Deploy this backend.
5. Confirm:
   - `GET /api/health` returns `{"status":"ok",...}`
   - Sign up creates a user.
   - Log out and log back in.
6. Do not put DATABASE_URL in HTML, JavaScript, or GitHub source.

The backend keeps SQLite as a local fallback when DATABASE_URL is absent.
