"""Vercel serverless entry point for Fantasy Football Draft Commander.

This module is the Vercel Python serverless function handler.
Vercel imports `app` from this module and uses it as a WSGI application.
"""

import os
import sys

# Ensure project root is on path so `src/` imports work
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Set Vercel environment flag so storage.py uses /tmp for league files
os.environ.setdefault("VERCEL_ENV", "1")
os.environ.setdefault("LEAGUES_DIR", "/tmp/leagues")

# Import the Flask app — this triggers route registration
from web_app import app

# Vercel expects `app` as the WSGI callable
# Flask's `app` object is already a WSGI application
handler = app
