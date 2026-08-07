"""Resource routes stub — extend for sales/purchases API later."""
from flask import Blueprint, jsonify

bp = Blueprint("resources", __name__, url_prefix="/api")


@bp.get("/ping")
def ping():
    return jsonify({"ok": True})
