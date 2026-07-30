#!/usr/bin/env python3
"""One-time migration: import users and leagues from the old file-based
storage into the database backend.

Safe to run multiple times — it skips users that already exist.

Usage:
    python scripts/migrate_to_db.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on path
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.database import db_create_user, db_get_user_by_email, init_db
from src.models import League


def migrate_users() -> int:
    """Migrate user accounts from users/<hash>/user.json files.

    Returns the number of users migrated.
    """
    users_dir = Path(_project_root) / "users"
    if not users_dir.exists():
        print("  → No users/ directory found. Skipping.")
        return 0

    count = 0
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        user_file = user_dir / "user.json"
        if not user_file.exists():
            continue

        with open(user_file) as f:
            data = json.load(f)

        email = data.get("email", "")
        if not email:
            continue
        if db_get_user_by_email(email):
            print(f"  SKIP  {email}  (already exists)")
            continue

        password_hash = data.get("password_hash", "")
        if password_hash:
            db_create_user(email, password_hash)
            print(f"  MIGRATED  {email}")
            count += 1
        else:
            print(f"  SKIP  {email}  (no password hash)")

    return count


def migrate_seed_users() -> int:
    """Migrate users from data/seed_users/ directory.

    Returns the number of users migrated.
    """
    seed_dir = Path(_project_root) / "data" / "seed_users"
    if not seed_dir.exists():
        print("  → No data/seed_users/ directory found. Skipping.")
        return 0

    count = 0
    for user_dir in seed_dir.iterdir():
        if not user_dir.is_dir():
            continue
        user_file = user_dir / "user.json"
        if not user_file.exists():
            continue

        with open(user_file) as f:
            data = json.load(f)

        email = data.get("email", "")
        if not email:
            continue
        if db_get_user_by_email(email):
            print(f"  SKIP  {email}  (already exists)")
            continue

        password_hash = data.get("password_hash", "")
        if password_hash:
            db_create_user(email, password_hash)
            print(f"  MIGRATED  {email}")
            count += 1

    return count


def main():
    print("=" * 60)
    print("  Fantasy Football — File-to-DB Migration")
    print("=" * 60)

    init_db()
    print("\n[1/2] Migrating user accounts...")
    n1 = migrate_users()
    n2 = migrate_seed_users()
    print(f"  → {n1 + n2} users migrated.\n")

    print("[2/2] Done!")
    print(f"\n  Summary: {n1 + n2} users imported into the database.")
    print("  Ready for the database-backed version of the app.\n")


if __name__ == "__main__":
    main()
