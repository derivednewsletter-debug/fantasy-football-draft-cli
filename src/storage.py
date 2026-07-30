"""Multi-league JSON persistence layer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from src.models import League, Player

# On Vercel (serverless), use /tmp/leagues since the filesystem is ephemeral.
# Locally, use the project's leagues/ directory.
_vercel_leagues = os.environ.get("LEAGUES_DIR")
if _vercel_leagues:
    LEAGUES_DIR = Path(_vercel_leagues)
else:
    LEAGUES_DIR = Path(__file__).resolve().parent.parent / "leagues"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# League persistence
# ---------------------------------------------------------------------------

def list_leagues() -> list[dict]:
    """Return metadata about all saved leagues."""
    os.makedirs(LEAGUES_DIR, exist_ok=True)
    leagues = []
    for fpath in sorted(LEAGUES_DIR.glob("*.json")):
        try:
            with open(fpath, "r") as f:
                data = json.load(f)
            leagues.append({
                "name": data.get("name", fpath.stem),
                "path": str(fpath),
                "num_teams": data.get("num_teams", 0),
                "scoring_format": data.get("scoring_format", "PPR"),
                "is_active": data.get("is_active", True),
                "completed": data.get("completed", False),
                "overall_pick": data.get("overall_pick", 0),
                "current_round": data.get("current_round", 0),
            })
        except (json.JSONDecodeError, IOError):
            continue
    return leagues


def save_league(league: League) -> None:
    """Persist a league to disk as JSON."""
    os.makedirs(LEAGUES_DIR, exist_ok=True)
    fpath = LEAGUES_DIR / f"{league.name}.json"
    with open(fpath, "w") as f:
        json.dump(league.to_dict(), f, indent=2)
    return fpath


def load_league(league_name: str) -> Optional[League]:
    """Load a league from disk by name."""
    fpath = LEAGUES_DIR / f"{league_name}.json"
    if not fpath.exists():
        # Try exact match or partial
        for f in LEAGUES_DIR.glob("*.json"):
            if league_name.lower() in f.stem.lower():
                fpath = f
                break
        else:
            return None

    with open(fpath, "r") as f:
        data = json.load(f)
    return League.from_dict(data)


def delete_league(league_name: str) -> bool:
    """Delete a saved league file."""
    fpath = LEAGUES_DIR / f"{league_name}.json"
    if fpath.exists():
        fpath.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Player data loading
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

    # Sort by tier then projected points descending
    players.sort(key=lambda p: (p.tier, -p.projected_points))
    return players
