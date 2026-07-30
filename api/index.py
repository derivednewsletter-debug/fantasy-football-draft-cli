"""Vercel serverless entry point for Fantasy Football Draft Commander.

This module is the Vercel Python serverless function handler.
Vercel imports `app` from this module and uses it as a WSGI application.
"""

import os
import shutil
import sys
from pathlib import Path

# Ensure project root is on path so `src/` imports work
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Set Vercel environment flags so storage/auth use /tmp for persistence
os.environ.setdefault("VERCEL_ENV", "1")
os.environ.setdefault("LEAGUES_DIR", "/tmp/leagues")
os.environ.setdefault("USERS_DIR", "/tmp/users")


# ---------------------------------------------------------------------------
# Seed users from the committed data/seed_users/ directory into /tmp/users
#
# The local users/ directory is gitignored for security, so pre-existing
# accounts can't live there in the deployed build.  Instead we keep a small
# seed file under data/seed_users/ (committed) and copy it into /tmp on
# every cold start so auth.py can find it on the writable filesystem.
# ---------------------------------------------------------------------------
def _seed_users():
    """Copy pre-existing user accounts from seed sources into /tmp/users."""
    dst_users = Path("/tmp/users")

    # Try the gitignored users/ directory first (local dev)
    src_candidates = [
        Path(_project_root) / "users",
        Path(_project_root) / "data" / "seed_users",
    ]

    any_seeded = False

    for src_users in src_candidates:
        if not src_users.exists():
            continue

        dst_users.mkdir(parents=True, exist_ok=True)

        for user_dir in src_users.iterdir():
            if not user_dir.is_dir():
                continue
            dst = dst_users / user_dir.name
            if not dst.exists():
                shutil.copytree(user_dir, dst, dirs_exist_ok=True)
                any_seeded = True

            # Also copy per-user leagues
            src_league_dir = user_dir / "leagues"
            if src_league_dir.exists():
                dst_league_dir = dst / "leagues"
                dst_league_dir.mkdir(parents=True, exist_ok=True)
                for f in src_league_dir.glob("*.json"):
                    dst_f = dst_league_dir / f.name
                    if not dst_f.exists():
                        shutil.copy2(f, dst_f)

    if any_seeded:
        print("[seed] User accounts seeded into /tmp/users")


_seed_users()

# Import the Flask app — this triggers route registration
from web_app import app

# Vercel expects `app` as the WSGI callable
# Flask's `app` object is already a WSGI application
handler = app
