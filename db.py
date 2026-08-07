"""Simple SQLite store for K-Ray auth."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("KRAY_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "kray.db"))
"""
app.py — K-Ray Enterprise backend entry point.

Run with:
    python app.py
or, for production-style serving:
    gunicorn "app:create_app()"

See README.md for full setup and API reference.
"""
import os
from flask import Flask, jsonify, send_from_directory

from db import init_db
from routes_auth import bp as auth_bp
from routes_resources import bp as resources_bp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
# Change this if your HTML file has a different name in /static
FRONTEND_FILENAME = os.environ.get("KRAY_FRONTEND_FILE", "abila.html")


def create_app():
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(resources_bp)

    # --- Manual CORS (no external dependency needed) ---------------------
    allowed_origin = os.environ.get("KRAY_CORS_ORIGIN", "*")

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def cors_preflight(_any):
        return ("", 204)

    # --- Health check -------------------------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "kray-enterprise-backend"})

    # --- Serve the frontend HTML file at the root URL ------------------------
    @app.get("/")
    def index():
        frontend_path = os.path.join(STATIC_DIR, FRONTEND_FILENAME)
        if os.path.exists(frontend_path):
            return send_from_directory(STATIC_DIR, FRONTEND_FILENAME)
        # Fallback if the HTML file isn't in /static yet, so you can see
        # what's wrong instead of a silent 404.
        return jsonify({
            "service": "K-Ray Enterprise backend",
            "error": f"Frontend file '{FRONTEND_FILENAME}' not found in /static. "
                     f"Upload it to the static/ folder in your repo (or set "
                     f"KRAY_FRONTEND_FILE to match its actual name).",
            "health": "/api/health",
        }), 200

    # --- Error handlers ----------------------------------------------------
    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def server_error(_e):
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)


"""
app.py — K-Ray Enterprise backend entry point.

Run with:
    python app.py
or, for production-style serving:
    gunicorn "app:create_app()"

See README.md for full setup and API reference.
"""
import os
from flask import Flask, jsonify, send_from_directory

from db import init_db
from routes_auth import bp as auth_bp
from routes_resources import bp as resources_bp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
# Change this if your HTML file has a different name in /static
FRONTEND_FILENAME = os.environ.get("KRAY_FRONTEND_FILE", "abila.html")


def create_app():
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(resources_bp)

    # --- Manual CORS (no external dependency needed) ---------------------
    allowed_origin = os.environ.get("KRAY_CORS_ORIGIN", "*")

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def cors_preflight(_any):
        return ("", 204)

    # --- Health check -------------------------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "kray-enterprise-backend"})

    # --- Serve the frontend HTML file at the root URL ------------------------
    @app.get("/")
    def index():
        frontend_path = os.path.join(STATIC_DIR, FRONTEND_FILENAME)
        if os.path.exists(frontend_path):
            return send_from_directory(STATIC_DIR, FRONTEND_FILENAME)
        # Fallback if the HTML file isn't in /static yet, so you can see
        # what's wrong instead of a silent 404.
        return jsonify({
            "service": "K-Ray Enterprise backend",
            "error": f"Frontend file '{FRONTEND_FILENAME}' not found in /static. "
                     f"Upload it to the static/ folder in your repo (or set "
                     f"KRAY_FRONTEND_FILE to match its actual name).",
            "health": "/api/health",
        }), 200

    # --- Error handlers ----------------------------------------------------
    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def server_error(_e):
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            business TEXT,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            email_verified INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS verifications (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            purpose TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER DEFAULT 0
        );
        """)
