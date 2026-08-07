"""
app.py — K-Ray Enterprise backend entry point.

Run with:
    python app.py
or, for production-style serving:
    gunicorn "app:create_app()"

See README.md for full setup and API reference.
"""
import os
from flask import Flask, jsonify,render_template

from db import init_db
from routes_auth import"""
app.py — K-Ray Enterprise backend entry point.

Run with:
    python app.py
or, for production-style serving:
    gunicorn "app:create_app()"

See README.md for full setup and API reference.
"""
import os
from flask import Flask, jsonify, render_template

from db import init_db
from routes_auth import bp as auth_bp
from routes_resources import bp as resources_bp


def create_app():
    # template_folder="." allows serving index.html directly from the same directory
    app = Flask(__name__, template_folder=".", static_folder=".")
    init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(resources_bp)

    # --- Manual CORS (no external dependency needed) ---------------------
    allowed_origin = os.environ.get("KRAY_CORS_ORIGIN", "*")

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Ty"""
app.py — K-Ray Enterprise backend entry point.

Run with:
    python app.py
or, for production-style serving:
    gunicorn "app:create_app()"

See README.md for full setup and API reference.
"""
import os
from flask import Flask, jsonify

from db import init_db
from routes_auth import bp as auth_bp
from routes_resources import bp as resources_bp


def create_app():
    app = Flask(__name__)
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

    # --- Health check + friendly root ------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "kray-enterprise-backend"})

    @app.get("/")
    def index():
        return jsonify({
            "service": "K-Ray Enterprise backend",
            "docs": "See README.md for the API reference",
            "health": "/api/health",
        })

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
pe, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def cors_preflight(_any):
        return ("", 204)

    # --- Health check + root static file serving -------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "kray-enterprise-backend"})

    @app.get("/")
    def index():
        return render_template("index.html")

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
    app.run(host="0.0.0.0", port=port, debug=debug) bp as auth_bp
from routes_resources import bp as resources_bp


def create_app():
    app = Flask(__name__)
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

    # --- Health check + friendly root ------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "kray-enterprise-backend"})

    @app.get("/")
    def index():
        return jsonify({
            "service": "K-Ray Enterprise backend",
            "docs": "See README.md for the API reference",
            "health": "/api/health",
        })

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
