#!/usr/bin/env python3
"""Fantasy Football Draft Commander — CLI entry point.

Run:
    python app.py

Requires: rich, pandas, thefuzz, python-Levenshtein
"""

from src.cli import run

if __name__ == "__main__":
    run()
