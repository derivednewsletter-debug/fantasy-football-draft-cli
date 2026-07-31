"""Vercel serverless entry point for Fantasy Football Draft Commander.

This module is the Vercel Python serverless function handler.
Vercel imports `app` from this module and uses it as a WSGI application.

Production database:
  Set DATABASE_URL to a Postgres connection string (e.g. from Neon) in the
  Vercel project's Environment Variables.  Without it the app falls back to
  a local SQLite file at data/app.db (which is ephemeral on Vercel).
"""

import json
import os
import sys
from pathlib import Path

# Ensure project root is on path so `src/` imports work
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ---------------------------------------------------------------------------
# Initialize the database on every cold start
#
# SQLite (local dev):  file created at data/app.db
# Postgres (Vercel):   set DATABASE_URL in Vercel env vars
# Tables are CREATE IF NOT EXISTS — safe to call on every boot.
# ---------------------------------------------------------------------------
from src.database import init_db, db_create_user, db_get_user_by_email

init_db()


# ---------------------------------------------------------------------------
# Seed pre-existing user accounts into the database (idempotent)
#
# Once a user exists in the database, subsequent cold starts skip them.
# Sources:
#   1. data/seed_users/  — committed to git, ships with every deployment
#   2. users/            — local only (gitignored), won't exist on Vercel
# ---------------------------------------------------------------------------
def _seed_users_from(source_dir: Path) -> None:
    """Copy user accounts from a directory into the database."""
    if not source_dir.exists():
        return
    for user_dir in source_dir.iterdir():
        if not user_dir.is_dir():
            continue
        user_file = user_dir / "user.json"
        if not user_file.exists():
            continue
        try:
            with open(user_file) as f:
                data = json.load(f)
            email = data.get("email", "")
            if not email or db_get_user_by_email(email):
                continue
            password_hash = data.get("password_hash", "")
            if password_hash:
                db_create_user(email, password_hash)
                print(f"[seed] Seeded {email} into database")
        except Exception as exc:
            print(f"[seed] Skipping {user_dir.name}: {exc}")


_seed_users_from(Path(_project_root) / "data" / "seed_users")
_seed_users_from(Path(_project_root) / "users")

# Import the Flask app — this triggers route registration
from web_app import app

# Vercel expects `app` as the WSGI callable
# Flask's `app` object is already a WSGI application
handler = app
