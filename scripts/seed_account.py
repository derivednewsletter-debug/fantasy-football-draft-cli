#!/usr/bin/env python3
"""Seed a user account — run this on any environment to create your login.

Usage:
    python scripts/seed_account.py

This creates the account in the environment-appropriate users directory
(local: project/users/, Vercel: /tmp/users/).
"""

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.auth import create_user

if __name__ == "__main__":
    email = "derivednewsletter@gmail.com"
    password = "Lsheats2010"

    success, message = create_user(email, password)
    if success:
        print(f"  ✓ Account created: {email}")
        print(f"  ✓ You can now sign in with your password.")
    elif "already exists" in message:
        print(f"  ✓ Account already exists: {email}")
        print(f"  ✓ Your account is ready to use.")
    else:
        print(f"  ✗ {message}")
        sys.exit(1)
