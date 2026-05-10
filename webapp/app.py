"""
Flask application factory and entry point for the DrugCLIP web application
(SSH/HPC edition).

Authentication mirrors app_ProtPrep: SQLite users, bcrypt passwords,
email verification via HPC SSH, and SSO from the ChemBioCatalyst portal.
Each user can only see and manage their own jobs.
"""

import logging
import os
import sqlite3
import traceback
import uuid

from flask import Flask, flash, redirect, render_template, request, session, url_for

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Application factory."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    app.config.from_object("webapp.config")

    # Ensure runtime directories exist
    for d in [
        app.config.get("SESSION_FILE_DIR", "webapp/flask_session"),
        app.config.get("UPLOAD_FOLDER", "webapp/uploads"),
        os.path.join(os.path.dirname(__file__), "data"),
    ]:
        os.makedirs(d, exist_ok=True)

    # Server-side sessions
    try:
        from flask_session import Session
        Session(app)
    except ImportError:
        logger.warning("flask-session not installed; server-side sessions disabled.")

    # ------------------------------------------------------------------
    # Jinja2 filters
    # ------------------------------------------------------------------

    @app.template_filter("datetimeformat")
    def datetimeformat(value: str) -> str:
        """Format an ISO 8601 timestamp into a human-readable string."""
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(value)
            dt = dt.astimezone(timezone.utc)
            return dt.strftime("%-d %b %Y, %H:%M UTC")
        except (ValueError, TypeError):
            return value or ""

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    from functools import wraps

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "email" not in session:
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    # ------------------------------------------------------------------
    # Auth routes
    # ------------------------------------------------------------------

    from webapp.config import (
        ADMIN_EMAIL, APP_BASE_URL, DB_FILE, PORTAL_URL, SECRET_KEY,
    )
    from webapp.modules.auth import (
        generate_token, hash_password, init_db,
        send_email_via_hpc, verify_password,
    )

    app.secret_key = SECRET_KEY
    init_db()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if "email" in session:
            return redirect(url_for("dashboard.index"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT password, verified FROM users WHERE email = ?", (email,))
            row = c.fetchone()
            conn.close()
            if row and verify_password(password, row[0]):
                if not row[1]:
                    flash("Please verify your email before logging in.", "warning")
                    return render_template("login.html")
                session["email"] = email
                return redirect(url_for("dashboard.index"))
            flash("Invalid email or password.", "danger")
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if "email" in session:
            return redirect(url_for("dashboard.index"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not email.endswith("@crick.ac.uk"):
                flash("Only @crick.ac.uk email addresses are allowed.", "danger")
                return render_template("register.html")
            if len(password) < 8:
                flash("Password must be at least 8 characters.", "danger")
                return render_template("register.html")
            if password != confirm:
                flash("Passwords do not match.", "danger")
                return render_template("register.html")
            hashed = hash_password(password)
            token = generate_token()
            try:
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute(
                    "INSERT INTO users (email, password, verification_code) VALUES (?, ?, ?)",
                    (email, hashed, token),
                )
                conn.commit()
                conn.close()
            except sqlite3.IntegrityError:
                flash("That email address is already registered.", "danger")
                return render_template("register.html")
            verify_url = f"{APP_BASE_URL}/verify/{token}"
            body = (
                f"Welcome to DrugCLIP!\n\n"
                f"Please verify your email by visiting:\n{verify_url}\n\n"
                f"— DrugCLIP Virtual Screening"
            )
            send_email_via_hpc(email, "[DrugCLIP] Verify your email", body)
            flash("Registration successful! Check your email to verify your account.", "success")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/verify/<token>")
    def verify_email(token: str):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            "UPDATE users SET verified = 1, verification_code = NULL WHERE verification_code = ?",
            (token,),
        )
        if c.rowcount > 0:
            conn.commit()
            flash("Email verified! You can now log in.", "success")
        else:
            flash("Invalid or expired verification link.", "danger")
        conn.close()
        return redirect(url_for("login"))

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/sso-login")
    def sso_login():
        """SSO entry point from the ChemBioCatalyst portal."""
        import requests as _requests
        token = request.args.get("token")
        if not token:
            flash("Invalid SSO token.", "danger")
            return redirect(url_for("login"))
        try:
            resp = _requests.post(
                f"{PORTAL_URL}/sso/api/validate",
                json={"token": token},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    user_email = data["user"]["email"]
                    conn = sqlite3.connect(DB_FILE)
                    c = conn.cursor()
                    c.execute("SELECT email FROM users WHERE email = ?", (user_email,))
                    if not c.fetchone():
                        import secrets as _secrets
                        c.execute(
                            "INSERT INTO users (email, password, verified) VALUES (?, ?, 1)",
                            (user_email, hash_password(_secrets.token_urlsafe(32))),
                        )
                        conn.commit()
                    conn.close()
                    session["email"] = user_email
                    return redirect(url_for("dashboard.index"))
        except Exception:
            pass
        flash("SSO login failed. Please try again or log in manually.", "danger")
        return redirect(url_for("login"))

    # ------------------------------------------------------------------
    # Admin panel
    # ------------------------------------------------------------------

    @app.route("/admin")
    @login_required
    def admin_panel():
        if session.get("email", "").lower() != ADMIN_EMAIL.lower():
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard.index"))
        from webapp.services.job_store import JobStore
        store_path = os.path.join(os.path.dirname(__file__), "data", "jobs.json")
        all_jobs = JobStore(store_path).get_all_jobs()
        return render_template("admin.html", jobs=all_jobs, admin_email=ADMIN_EMAIL,
                               current_user=session["email"])

    # ------------------------------------------------------------------
    # Protect dashboard and job routes
    # ------------------------------------------------------------------

    @app.before_request
    def require_login():
        """Redirect unauthenticated users to /login for protected routes."""
        public = {"login", "register", "verify_email", "sso_login",
                  "static", "help.index"}
        if request.endpoint and request.endpoint not in public:
            if "email" not in session:
                return redirect(url_for("login"))

    # ------------------------------------------------------------------
    # Register blueprints
    # ------------------------------------------------------------------

    from webapp.routes.dashboard import dashboard_bp
    from webapp.routes.help import help_bp
    from webapp.routes.jobs import jobs_bp
    from webapp.routes.logs import logs_bp
    from webapp.routes.results import results_bp
    from webapp.routes.upload import upload_bp
    from webapp.routes.api import api_bp
    from webapp.routes.docking import docking_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(help_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(docking_bp)

    # ------------------------------------------------------------------
    # Initialize services
    # ------------------------------------------------------------------

    from webapp.services.job_monitor import JobMonitor
    from webapp.services.job_store import JobStore
    from webapp.services.job_submission import JobSubmissionService
    from webapp.services.slurm_client import SlurmClient

    store_path = os.path.join(os.path.dirname(__file__), "data", "jobs.json")
    slurm_client = SlurmClient()
    job_store = JobStore(store_path)
    job_submission_service = JobSubmissionService(slurm_client, job_store, PROJECT_ROOT)

    poll_interval = app.config.get("POLL_INTERVAL", 120)
    job_monitor = JobMonitor(slurm_client, job_store, poll_interval=poll_interval)

    app.extensions["slurm_client"] = slurm_client
    app.extensions["job_store"] = job_store
    app.extensions["job_submission_service"] = job_submission_service
    app.extensions["job_monitor"] = job_monitor

    # ------------------------------------------------------------------
    # Start background threads
    # ------------------------------------------------------------------

    job_monitor.start()
    logger.info("JobMonitor started (poll_interval=%ds).", poll_interval)

    from webapp.modules.safety_monitor import start_safety_monitor
    start_safety_monitor()
    logger.info("Safety monitor started.")

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(403)
    def forbidden(error):
        return render_template(
            "error.html", code=403,
            message="You do not have permission to access this resource.",
        ), 403

    @app.errorhandler(500)
    def internal_server_error(error):
        ref_id = str(uuid.uuid4())
        logger.error("Internal server error [ref=%s]: %s\n%s",
                     ref_id, error, traceback.format_exc())
        return render_template(
            "error.html", code=500, message="Something went wrong.", ref_id=ref_id,
        ), 500

    return app


# ---------------------------------------------------------------------------
# Module-level PROJECT_ROOT (needed by routes)
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
