"""Help page route handler for the DrugCLIP web application.

Requirements: 13.1
"""

from flask import Blueprint, render_template

help_bp = Blueprint("help", __name__, url_prefix="/help")


@help_bp.route("/", methods=["GET"])
def index():
    """Render the help/documentation page."""
    return render_template("help.html")
