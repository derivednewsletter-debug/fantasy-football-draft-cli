"""Live NFL data from ESPN's free public API — no API key required.

Endpoints used (all free, undocumented, no auth):
  - Scoreboard:   site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard
  - Game summary: site.web.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=<ID>
  - Athlete info: site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/<ID>
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_GAME_SUMMARY = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={}"
ESPN_ATHLETE = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{}"

# Simple in-memory cache: key -> (timestamp, data)
_cache: dict[str, tuple[float, any]] = {}
CACHE_TTL = 60  # seconds


def _fetch(url: str, cache_ttl: int = CACHE_TTL) -> Optional[dict]:
    """Fetch JSON from a URL with caching."""
    now = time.time()
    if url in _cache and (now - _cache[url][0]) < cache_ttl:
        return _cache[url][1]

    try:
        req = Request(url, headers={"User-Agent": "DraftElite/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        _cache[url] = (now, data)
        return data
    except Exception:
        return None


# ---------------------------------------------------------------------------
# NFL season state
# ---------------------------------------------------------------------------

def get_nfl_state() -> dict:
    """Return current NFL season info: week, season_type, season_year."""
    data = _fetch(ESPN_SCOREBOARD, cache_ttl=300)
    if not data:
        return {"week": 1, "season_type": 2, "season_year": 2024, "error": True}

    season = data.get("season", {})
    week = data.get("week", {})

    return {
        "week": week.get("number", 1),
        "season_type": season.get("type", 2),
        "season_year": season.get("year", 2024),
        "error": False,
    }


# ---------------------------------------------------------------------------
# Live scoreboard
# ---------------------------------------------------------------------------

def get_live_scores() -> list[dict]:
    """Get all NFL games with live scores."""
    data = _fetch(ESPN_SCOREBOARD, cache_ttl=30)
    if not data:
        return []

    games = []
    for event in data.get("events", []):
        competition = event.get("competitions", [{}])[0]
        competitors = competition.get("competitors", [])

        home = None
        away = None
        for c in competitors:
            team_data = {
                "id": c.get("id"),
                "name": c.get("team", {}).get("displayName", "TBD"),
                "abbreviation": c.get("team", {}).get("abbreviation", "TBD"),
                "score": c.get("score", "0"),
                "is_home": c.get("homeAway") == "home",
                "record": c.get("records", [{}])[0].get("summary", "") if c.get("records") else "",
            }
            if team_data["is_home"]:
                home = team_data
            else:
                away = team_data

        status = competition.get("status", {})
        period = status.get("period", 0)
        clock = status.get("displayClock", "00:00")

        # Determine game state
        state = status.get("type", {}).get("name", "pre")
        if state == "STATUS_FINAL":
            game_state = "FINAL"
        elif state == "STATUS_IN_PROGRESS":
            game_state = "LIVE"
        elif state == "STATUS_SCHEDULED":
            game_state = "UPCOMING"
        else:
            game_state = state.replace("STATUS_", "")

        games.append({
            "id": event.get("id"),
            "home": home,
            "away": away,
            "state": game_state,
            "period": period,
            "clock": clock,
            "date": competition.get("date", ""),
            "week": data.get("week", {}).get("number", 1),
        })

    return games


# ---------------------------------------------------------------------------
# Player game stats
# ---------------------------------------------------------------------------

def get_player_stats_in_game(event_id: str) -> list[dict]:
    """Get player statistics for a specific game."""
    data = _fetch(ESPN_GAME_SUMMARY.format(event_id), cache_ttl=30)
    if not data:
        return []

    players = []
    for h2h in data.get("boxscore", {}).get("players", []):
        team_id = h2h.get("team", {}).get("id")
        for stat_entry in h2h.get("statistics", []):
            for athlete_entry in stat_entry.get("athletes", []):
                athlete = athlete_entry.get("athlete", {})
                stats = athlete_entry.get("stats", [])

                # Parse stat labels and values
                stat_dict = {}
                labels = stat_entry.get("labels", [])
                for i, label in enumerate(labels):
                    if i < len(stats):
                        stat_dict[label.lower()] = stats[i]

                player = {
                    "id": athlete.get("id"),
                    "name": athlete.get("displayName", "Unknown"),
                    "position": athlete.get("position", {}).get("abbreviation", ""),
                    "headshot": athlete.get("headshot", ""),
                    "team_id": team_id,
                    "stats": stat_dict,
                }

                # Calculate fantasy points (simple PPR)
                fp = 0.0
                fp += int(stat_dict.get("passingTouchdowns", 0)) * 4
                fp += int(stat_dict.get("rushingTouchdowns", 0)) * 6
                fp += int(stat_dict.get("receivingTouchdowns", 0)) * 6
                fp += float(stat_dict.get("passingYards", 0)) * 0.04
                fp += float(stat_dict.get("rushingYards", 0)) * 0.1
                fp += float(stat_dict.get("receivingYards", 0)) * 0.1
                fp += int(stat_dict.get("receptions", 0)) * 1  # PPR
                fp -= int(stat_dict.get("passingInterceptions", 0)) * 2
                fp -= int(stat_dict.get("fumblesLost", 0)) * 2

                player["fantasy_points"] = round(fp, 1)
                players.append(player)

    return players


# ---------------------------------------------------------------------------
# League-specific matchup simulation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Scoring alerts (generated from live data)
# ---------------------------------------------------------------------------

def get_scoring_alerts(games: list[dict]) -> list[dict]:
    """Generate scoring alerts from live game data."""
    alerts = []
    for game in games:
        if game["state"] != "LIVE":
            continue
        stats = get_player_stats_in_game(game["id"])
        for p in stats[:3]:  # Top 3 players
            if p["fantasy_points"] >= 10:
                td_type = None
                td_count = int(p["stats"].get("receivingTouchdowns", 0)) + \
                           int(p["stats"].get("rushingTouchdowns", 0)) + \
                           int(p["stats"].get("passingTouchdowns", 0))
                if td_count > 0:
                    td_type = "TOUCHDOWN"
                elif p["fantasy_points"] >= 15:
                    td_type = "BIG GAME"

                if td_type:
                    alerts.append({
                        "type": td_type,
                        "player": p["name"],
                        "position": p["position"],
                        "detail": f"{p['fantasy_points']} fantasy pts",
                        "game": f"{game['away']['abbreviation']} @ {game['home']['abbreviation']}",
                        "time": f"Q{game['period']} {game['clock']}",
                    })
    return alerts[:10]
