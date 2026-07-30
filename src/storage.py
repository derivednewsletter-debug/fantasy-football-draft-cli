"""
Multi-league persistence layer backed by the database (SQLite / Postgres).

Replaces the old file-based JSON storage.  Leagues are scoped to the current
user automatically via :func:`set_current_user`, which must be called at the
start of each authenticated request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.auth import get_current_user_id, set_current_user as _set_current_user
from src.database import (
    db_delete_league,
    db_list_leagues,
    db_load_league,
    db_save_league,
)
from src.models import League, Player

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# User scoping (delegates to auth module)
# ---------------------------------------------------------------------------

def set_user_leagues_dir(user_email: str | None) -> None:
    """Scope league storage to a specific user.

    **Note:** The name is historical — we no longer use a directory-based
    approach.  This function sets the current user id in the database layer.
    """
    _set_current_user(user_email)


# ---------------------------------------------------------------------------
# League persistence
# ---------------------------------------------------------------------------


def list_leagues() -> list[dict]:
    """Return metadata about all saved leagues for the current user."""
    uid = get_current_user_id()
    if uid is None:
        return []
    return db_list_leagues(uid)


def save_league(league: League) -> None:
    """Persist a league to the database."""
    uid = get_current_user_id()
    if uid is None:
        raise RuntimeError("No user session — call set_user_leagues_dir first.")
    league_dict = _clean_league_dict(league.to_dict())
    db_save_league(uid, league.name, league_dict)


def load_league(league_name: str) -> Optional[League]:
    """Load a league from the database by name."""
    uid = get_current_user_id()
    if uid is None:
        return None

    data = db_load_league(uid, league_name)
    if data is not None:
        return League.from_dict(data)

    # Fallback: try partial name match
    for meta in db_list_leagues(uid):
        if league_name.lower() in meta["name"].lower():
            data = db_load_league(uid, meta["name"])
            if data is not None:
                return League.from_dict(data)
    return None


def delete_league(league_name: str) -> bool:
    """Delete a saved league. Returns True if deleted."""
    uid = get_current_user_id()
    if uid is None:
        return False
    return db_delete_league(uid, league_name)


# ---------------------------------------------------------------------------
# Player data loading (unchanged — CSV file on disk)
# ---------------------------------------------------------------------------


def load_player_data(filepath: Optional[str] = None) -> list[Player]:
    """Load players from a CSV projections file, or fall back to defaults."""
    if filepath is None:
        candidates = [
            DATA_DIR / "default_projections.csv",
            Path("data/default_projections.csv"),
            Path("data/projections.csv"),
        ]
        for c in candidates:
            if c.exists():
                filepath = str(c)
                break
        else:
            raise FileNotFoundError("No player data file found. Place a CSV in data/")

    import csv
    players = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                player = Player(
                    name=row["name"].strip(),
                    position=row["position"].strip(),
                    team=row["team"].strip(),
                    projected_points=float(row.get("projected_points", 0)),
                    adp=float(row.get("adp", 999)),
                    tier=int(row.get("tier", 5)),
                )
                players.append(player)
            except (ValueError, KeyError):
                continue

    players.sort(key=lambda p: (p.tier, -p.projected_points))
    return players


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_league_dict(d: dict) -> dict:
    """Remove keys that can't be JSON-serialized (e.g. some dataclasses)."""
    # The League.to_dict() already returns plain Python types, so this is
    # mostly a safety net.  Convert any remaining non-serializable values.
    try:
        json.dumps(d)
        return d
    except (TypeError, ValueError):
        # Deep-convert — replace non-serializable values with str()
        return json.loads(json.dumps(d, default=str))
