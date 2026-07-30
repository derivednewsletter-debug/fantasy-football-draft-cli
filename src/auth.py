"""User authentication module — email/password registration, login, session management.

Uses the database backend (``src.database``) instead of file-based storage,
so user data survives Vercel cold starts when connected to Postgres.
"""

from __future__ import annotations

import re
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

from src.database import (
    db_create_user,
    db_get_user_by_email,
    db_user_exists,
    init_db,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMAIL_PATTERN = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"


def _validate_email(email: str) -> bool:
    """Basic email format validation."""
    return bool(re.match(EMAIL_PATTERN, email.strip()))


def _validate_password(password: str) -> tuple[bool, str]:
    """Validate password strength. Returns (valid, message)."""
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    return True, ""


# ---------------------------------------------------------------------------
# Public API (unchanged signatures from the file-based version)
# ---------------------------------------------------------------------------


def create_user(email: str, password: str) -> tuple[bool, str]:
    """Register a new user. Returns (success, message)."""
    email = email.lower().strip()

    if not _validate_email(email):
        return False, "Invalid email format."

    valid, msg = _validate_password(password)
    if not valid:
        return False, msg

    if db_user_exists(email):
        return False, "An account with this email already exists."

    password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    db_create_user(email, password_hash)
    return True, "Account created! You can now log in."


def verify_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials. Returns user dict on success, None on failure."""
    email = email.lower().strip()
    user = db_get_user_by_email(email)
    if user is None:
        return None

    if check_password_hash(user["password_hash"], password):
        return {"email": user["email"], "id": user["id"]}

    return None


def get_user_data(email: str) -> Optional[dict]:
    """Load user info (without password hash). Returns None if not found."""
    user = db_get_user_by_email(email.lower().strip())
    if user is None:
        return None
    return {"email": user["email"], "id": user["id"]}


def user_exists(email: str) -> bool:
    """Check if a user account exists."""
    return db_user_exists(email.lower().strip())


def get_user_id(email: str) -> Optional[int]:
    """Return the numeric user id for an email, or None."""
    user = db_get_user_by_email(email.lower().strip())
    return user["id"] if user else None


# ---------------------------------------------------------------------------
# Session / connection helpers (for storage.py integration)
# ---------------------------------------------------------------------------

_current_user_id: int | None = None


def set_current_user(email: str | None) -> None:
    """Set the globally-scoped current user id for league storage operations.

    Call this at the start of each request (or pass ``None`` to reset).
    """
    global _current_user_id
    if email:
        _current_user_id = get_user_id(email)
    else:
        _current_user_id = None


def get_current_user_id() -> int | None:
    """Return the currently-scoped user id (or ``None``)."""
    return _current_user_id


def email_to_hash(email: str) -> str:
    """Create a safe directory name from an email — kept for backward compat."""
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()[:24]
