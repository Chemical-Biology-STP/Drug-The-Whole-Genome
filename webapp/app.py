"""
Flask application factory and entry point for the DrugCLIP web application.

Requirements: 1.2, 2.3
"""

import logging
import os
import traceback
import uuid

from flask import Flask, render_template, send_from_directory

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Application factory.

    Creates and configures the Flask application, registers blueprints,
    sets up server-side sessions, initializes services, and starts the
    background job monitor.

    Returns
    -------
    Flask
        The configured Flask application instance.
    """
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ------------------------------------------------------------------
    # Load configuration from webapp/config.py
    # ------------------------------------------------------------------
    app.config.from_object("webapp.config")

    # Ensure the session file directory exists
    session_dir = app.config.get("SESSION_FILE_DIR", "webapp/flask_session")
    os.makedirs(session_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Server-side sessions (flask-session, filesystem backend)
    # ------------------------------------------------------------------
    try:
        from flask_session import Session  # type: ignore[import]

        Session(app)
    except ImportError:
        logger.warning(
            "flask-session is not installed; server-side sessions are disabled. "
            "Install it with: pip install flask-session"
        )

    # ------------------------------------------------------------------
    # Jinja2 filters
    # ------------------------------------------------------------------

    @app.template_filter("datetimeformat")
    def datetimeformat(value: str) -> str:
        """Format an ISO 8601 timestamp into a human-readable string.

        e.g. "2026-04-30T13:28:53.072353+00:00" → "30 Apr 2026, 13:28 UTC"
        """
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(value)
            dt = dt.astimezone(timezone.utc)
            return dt.strftime("%-d %b %Y, %H:%M UTC")
        except (ValueError, TypeError):
            return value

    # ------------------------------------------------------------------
    # Register blueprints
    # ------------------------------------------------------------------

    from webapp.routes.dashboard import dashboard_bp
    from webapp.routes.help import help_bp
    from webapp.routes.jobs import jobs_bp
    from webapp.routes.logs import logs_bp
    from webapp.routes.results import results_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(help_bp)

    # Serve the built MkDocs site at /docs/
    docs_dir = os.path.join(os.path.dirname(__file__), "static", "docs")

    @app.route("/docs/")
    @app.route("/docs/<path:filename>")
    def docs(filename="index.html"):
        return send_from_directory(docs_dir, filename)

    logger.debug("Registered all blueprints: dashboard, jobs, results, logs, help")

    # ------------------------------------------------------------------
    # Initialize services and attach to app context
    # ------------------------------------------------------------------

    from webapp.services.file_upload import FileUploadHandler
    from webapp.services.job_monitor import JobMonitor
    from webapp.services.job_store import JobStore
    from webapp.services.job_submission import JobSubmissionService
    from webapp.services.slurm_client import SlurmClient

    store_path = os.path.join(os.path.dirname(__file__), "data", "jobs.json")
    slurm_client = SlurmClient()
    job_store = JobStore(store_path)
    file_upload_handler = FileUploadHandler()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    job_submission_service = JobSubmissionService(slurm_client, job_store, project_root)

    poll_interval = app.config.get("POLL_INTERVAL", 30)
    job_monitor = JobMonitor(slurm_client, job_store, poll_interval=poll_interval)

    # Attach services to app.extensions for access from route handlers
    app.extensions["slurm_client"] = slurm_client
    app.extensions["job_store"] = job_store
    app.extensions["file_upload_handler"] = file_upload_handler
    app.extensions["job_submission_service"] = job_submission_service
    app.extensions["job_monitor"] = job_monitor

    # ------------------------------------------------------------------
    # Start background job monitor
    # ------------------------------------------------------------------

    job_monitor.start()
    logger.info("JobMonitor background thread started (poll_interval=%ds).", poll_interval)

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 Not Found errors."""
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(403)
    def forbidden(error):
        """Handle 403 Forbidden errors."""
        return (
            render_template(
                "error.html",
                code=403,
                message="You do not have permission to access this resource.",
            ),
            403,
        )

    @app.errorhandler(500)
    def internal_server_error(error):
        """Handle 500 Internal Server Error.

        Generates a UUID reference ID, logs the full traceback, and renders
        a generic error page that shows only the reference ID to the user.
        """
        ref_id = str(uuid.uuid4())
        logger.error(
            "Internal server error [ref=%s]: %s\n%s",
            ref_id,
            error,
            traceback.format_exc(),
        )
        return (
            render_template(
                "error.html",
                code=500,
                message="Something went wrong.",
                ref_id=ref_id,
            ),
            500,
        )

    return app


# ---------------------------------------------------------------------------
# Development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    # Ensure the project root is on sys.path so that "webapp" is importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
