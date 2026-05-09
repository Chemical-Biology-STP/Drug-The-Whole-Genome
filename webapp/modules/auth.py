"""Authentication helpers for DrugCLIP — SQLite users, bcrypt, email via HPC SSH."""

from __future__ import annotations

import os
import secrets
import sqlite3

import bcrypt

from webapp.config import ADMIN_EMAIL, APP_BASE_URL, DB_FILE, REMOTE_HOST, REMOTE_USER


def init_db() -> None:
    """Create the users table if it doesn't exist."""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            email             TEXT UNIQUE NOT NULL,
            password          TEXT NOT NULL,
            verified          INTEGER DEFAULT 0,
            verification_code TEXT,
            reset_code        TEXT
        )
    """)
    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def generate_token() -> str:
    return secrets.token_urlsafe(16)


def send_email_via_hpc(to_email: str, subject: str, body: str) -> tuple[bool, str | None]:
    """Send an email using the HPC mail command via SSH."""
    from webapp.modules.remote_server import RemoteServer
    try:
        server = RemoteServer(REMOTE_HOST, REMOTE_USER)
        escaped_body = body.replace("'", "'\\''").replace('"', '\\"')
        escaped_subject = subject.replace("'", "'\\''").replace('"', '\\"')
        command = f'echo "{escaped_body}" | mail -s "{escaped_subject}" {to_email}'
        output, error = server.run_command(command, timeout=30)
        if error:
            return False, f"HPC mail error: {error}"
        return True, None
    except Exception as exc:
        return False, str(exc)
