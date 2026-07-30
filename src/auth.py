"""User authentication module — email/password registration, login, session management."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

# ---------------------------------------------------------------------------
# User storage directory
# ---------------------------------------------------------------------------

# On Vercel, use /tmp/users; locally, use the project's users/ directory
USERS_DIR = Path(os.environ.get("USERS_DIR", Path(__file__).resolve().parent.parent / "users"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _email_to_dir(email: str) -> str:
    """Create a safe directory name from an email address (SHA-256 hash)."""
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()[:24]


def _validate_email(email: str) -> bool:
    """Basic email format validation."""
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email.strip()))


def _validate_password(password: str) -> tuple[bool, str]:
    """Validate password strength. Returns (valid, message)."""
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    return True, ""


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

def create_user(email: str, password: str) -> tuple[bool, str]:
    """Register a new user. Returns (success, message)."""
    email = email.lower().strip()

    if not _validate_email(email):
        return False, "Invalid email format."

    valid, msg = _validate_password(password)
    if not valid:
        return False, msg

    user_dir = USERS_DIR / _email_to_dir(email)
    user_file = user_dir / "user.json"

    if user_file.exists():
        return False, "An account with this email already exists."

    os.makedirs(user_dir, exist_ok=True)

    user_data = {
        "email": email,
        "password_hash": generate_password_hash(password),
        "leagues": {},
    }

    with open(user_file, "w") as f:
        json.dump(user_data, f, indent=2)

    # Create leagues subdirectory for this user
    leagues_dir = user_dir / "leagues"
    os.makedirs(leagues_dir, exist_ok=True)

    return True, "Account created! You can now log in."


def verify_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials. Returns user dict on success, None on failure."""
    email = email.lower().strip()
    user_dir = USERS_DIR / _email_to_dir(email)
    user_file = user_dir / "user.json"

    if not user_file.exists():
        return None

    with open(user_file, "r") as f:
        user_data = json.load(f)

    if check_password_hash(user_data["password_hash"], password):
        # Return user info without the password hash
        return {"email": user_data["email"]}

    return None


def get_user_data(email: str) -> Optional[dict]:
    """Load user data (without password hash). Returns None if not found."""
    email = email.lower().strip()
    user_dir = USERS_DIR / _email_to_dir(email)
    user_file = user_dir / "user.json"

    if not user_file.exists():
        return None

    with open(user_file, "r") as f:
        data = json.load(f)

    return {"email": data["email"]}


def get_user_leagues_dir(email: str) -> Path:
    """Get the leagues directory for a given user."""
    user_dir = USERS_DIR / _email_to_dir(email)
    leagues_dir = user_dir / "leagues"
    os.makedirs(leagues_dir, exist_ok=True)
    return leagues_dir


def user_exists(email: str) -> bool:
    """Check if a user account exists."""
    email = email.lower().strip()
    user_file = USERS_DIR / _email_to_dir(email) / "user.json"
    return user_file.exists()
