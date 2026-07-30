"""Dataclass models for Fantasy Football Draft CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional

from thefuzz import process as fuzz_process


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

@dataclass
class Player:
    """A single fantasy-relevant player (or DST)."""
    name: str
    position: str          # QB, RB, WR, TE, K, DST
    team: str
    projected_points: float = 0.0
    adp: float = 999.0      # average draft position (overall)
    tier: int = 5
    is_drafted: bool = False
    drafted_by: Optional[int] = None  # team number
    drafted_at_pick: Optional[int] = None

    @property
    def sort_key(self) -> tuple:
        """Sort by tier first, then projected_points descending."""
        return (self.tier, -self.projected_points)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Player:
        return cls(**data)


# ---------------------------------------------------------------------------
# Pick
# ---------------------------------------------------------------------------

@dataclass
class Pick:
    """A single draft selection."""
    overall_pick: int
    round_number: int
    team_number: int
    player_name: str
    player_position: str
    player_team: str
    projected_points: float = 0.0

    def to_dict(self) -> dict:
        return {
            "overall_pick": self.overall_pick,
            "round_number": self.round_number,
            "team_number": self.team_number,
            "player_name": self.player_name,
            "player_position": self.player_position,
            "player_team": self.player_team,
            "projected_points": self.projected_points,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Pick:
        return cls(**data)


# ---------------------------------------------------------------------------
# RosterConstruction
# ---------------------------------------------------------------------------

ROSTER_PRESETS: dict[str, dict[str, int]] = {
    "PPR":        {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "BENCH": 6},
    "0.5_PPR":    {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "BENCH": 6},
    "Standard":   {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "BENCH": 6},
    "2QB/Superflex": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "BENCH": 7},
}


def pos_flex_eligible(pos: str) -> list[str]:
    """Return the roster slots a given position can fill."""
    if pos == "QB":
        return ["QB", "FLEX"]
    elif pos == "RB":
        return ["RB", "FLEX"]
    elif pos == "WR":
        return ["WR", "FLEX"]
    elif pos == "TE":
        return ["TE", "FLEX"]
    elif pos == "K":
        return ["K"]
    elif pos == "DST":
        return ["DST"]
    return []


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

@dataclass
class Team:
    """One team's roster state within a league."""
    number: int
    name: str = ""
    roster: list[Player] = field(default_factory=list)
    roster_slots: dict[str, int] = field(default_factory=lambda: dict(ROSTER_PRESETS["PPR"]))
    waiver_moves: int = 0
    faab_budget: float = 100.0
    waiver_priority: int = 999

    def __post_init__(self):
        if not self.name:
            self.name = f"Team {self.number}"

    @property
    def starters(self) -> list[Player]:
        """Return the current best guess at starters based on roster slots."""
        return self.roster[:self.starting_slots_count]

    @property
    def starting_slots_count(self) -> int:
        """Total number of non-bench slots."""
        return sum(v for k, v in self.roster_slots.items() if k != "BENCH")

    @property
    def bench_count(self) -> int:
        return self.roster_slots.get("BENCH", 6)

    @property
    def is_full(self) -> bool:
        return len(self.roster) >= self.total_slots

    @property
    def total_slots(self) -> int:
        return sum(self.roster_slots.values())

    def roster_count_by_position(self, pos: str) -> int:
        return sum(1 for p in self.roster if p.position == pos)

    def starter_count_by_position(self, pos: str) -> int:
        """Count how many of a position are in starting slots."""
        starters = self.roster[:self.starting_slots_count]
        return sum(1 for p in starters if p.position == pos)

    def needed_starters(self, pos: str) -> int:
        """How many more starters needed at this position."""
        required = 0
        if pos == "FLEX":
            # FLEX can be RB/WR/TE
            return 0  # handled differently
        required = self.roster_slots.get(pos, 0)
        filled = self.starter_count_by_position(pos)
        return max(0, required - filled)

    def needs_position(self, pos: str) -> bool:
        """True if this position still needs roster depth."""
        slots = self.roster_slots
        if pos in ("QB", "RB", "WR", "TE", "K", "DST"):
            total_needed = slots.get(pos, 0)
            have = self.roster_count_by_position(pos)
            if have < total_needed:
                return True
            # Also check if we could use another for FLEX
            flex_slots = slots.get("FLEX", 0)
            flex_eligible = [p for p in self.roster if p.position in ("RB", "WR", "TE")]
            flex_filled = len(flex_eligible)
            if have > total_needed and (have - total_needed) + flex_filled < flex_slots + total_needed:
                return True
        return False

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "name": self.name,
            "roster": [p.to_dict() for p in self.roster],
            "roster_slots": dict(self.roster_slots),
            "waiver_moves": self.waiver_moves,
            "faab_budget": self.faab_budget,
            "waiver_priority": self.waiver_priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Team:
        team = cls(number=data["number"], name=data.get("name", ""), roster_slots=data.get("roster_slots", {}))
        team.roster = [Player.from_dict(p) for p in data.get("roster", [])]
        team.waiver_moves = data.get("waiver_moves", 0)
        team.faab_budget = data.get("faab_budget", 100.0)
        team.waiver_priority = data.get("waiver_priority", 999)
        return team


# ---------------------------------------------------------------------------
# League
# ---------------------------------------------------------------------------

@dataclass
class League:
    """Complete league configuration + live draft state."""
    name: str
    num_teams: int
    user_team_number: int          # 1-indexed
    scoring_format: str            # PPR, 0.5_PPR, Standard, 2QB/Superflex
    roster_slots: dict[str, int]   # e.g. {"QB":1, "RB":2, ...}
    teams: list[Team] = field(default_factory=list)
    players_pool: list[Player] = field(default_factory=list)  # all available players (undrafted)
    draft_log: list[Pick] = field(default_factory=list)
    current_round: int = 1
    current_pick_in_round: int = 1   # 1-indexed within the round
    overall_pick: int = 1
    is_active: bool = True
    completed: bool = False
    schedule: dict[str, list[dict]] = field(default_factory=dict)  # week -> [{home_team, away_team}, ...]
    current_week: int = 1
    week_opponent: Optional[int] = None  # opponent team number for current week
    matchup_results: dict[str, dict] = field(default_factory=dict)
    # matchup_results format: {week_str: {team_num: {"pf": float, "pa": float, "result": "W"|"L"|"T"}}}
    waiver_log: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.teams:
            for i in range(1, self.num_teams + 1):
                team = Team(
                    number=i,
                    name=f"Team {i}",
                    roster_slots=dict(self.roster_slots),
                )
                self.teams.append(team)

    @property
    def user_team(self) -> Team:
        return self.teams[self.user_team_number - 1]

    @property
    def team_on_clock(self) -> int:
        """Return the 1-indexed team number currently picking."""
        if self.current_round % 2 == 1:
            # Odd rounds: 1 → N
            return self.current_pick_in_round
        else:
            # Even rounds: N → 1
            return self.num_teams - self.current_pick_in_round + 1

    @property
    def is_user_on_clock(self) -> bool:
        return self.team_on_clock == self.user_team_number

    @property
    def available_players(self) -> list[Player]:
        return [p for p in self.players_pool if not p.is_drafted]

    def advance_pick(self):
        """Advance the draft state by one pick."""
        self.overall_pick += 1
        self.current_pick_in_round += 1
        if self.current_pick_in_round > self.num_teams:
            self.current_pick_in_round = 1
            self.current_round += 1

    def regress_pick(self):
        """Go back one pick (for undo)."""
        self.overall_pick -= 1
        self.current_pick_in_round -= 1
        if self.current_pick_in_round < 1:
            self.current_round -= 1
            self.current_pick_in_round = self.num_teams

    def record_pick(self, player_name: str) -> Optional[Pick]:
        """Record a pick for the team on the clock. Returns the Pick or None on failure."""
        # Fuzzy match the player name
        if not self.available_players:
            return None

        names = [p.name for p in self.available_players]
        match, score = fuzz_process.extractOne(player_name, names, score_cutoff=60)
        if match is None:
            return None

        player = next(p for p in self.available_players if p.name == match)

        team_num = self.team_on_clock
        team = self.teams[team_num - 1]

        player.is_drafted = True
        player.drafted_by = team_num
        player.drafted_at_pick = self.overall_pick

        team.roster.append(player)
        team.waiver_moves += 1

        pick = Pick(
            overall_pick=self.overall_pick,
            round_number=self.current_round,
            team_number=team_num,
            player_name=player.name,
            player_position=player.position,
            player_team=player.team,
            projected_points=player.projected_points,
        )
        self.draft_log.append(pick)
        self.advance_pick()
        return pick

    def undo_last_pick(self) -> bool:
        """Undo the most recent pick. Returns True on success."""
        if not self.draft_log:
            return False

        last_pick = self.draft_log.pop()
        self.regress_pick()

        # Find and un-draft the player
        player = next(
            (p for p in self.players_pool if p.name == last_pick.player_name),
            None
        )
        if player:
            player.is_drafted = False
            player.drafted_by = None
            player.drafted_at_pick = None

        # Remove from team roster
        team = self.teams[last_pick.team_number - 1]
        team.roster = [p for p in team.roster if p.name != last_pick.player_name]
        return True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "num_teams": self.num_teams,
            "user_team_number": self.user_team_number,
            "scoring_format": self.scoring_format,
            "roster_slots": dict(self.roster_slots),
            "teams": [t.to_dict() for t in self.teams],
            "players_pool": [p.to_dict() for p in self.players_pool],
            "draft_log": [p.to_dict() for p in self.draft_log],
            "current_round": self.current_round,
            "current_pick_in_round": self.current_pick_in_round,
            "overall_pick": self.overall_pick,
            "is_active": self.is_active,
            "completed": self.completed,
            "schedule": self.schedule,
            "current_week": self.current_week,
            "week_opponent": self.week_opponent,
            "matchup_results": self.matchup_results,
            "waiver_log": self.waiver_log,
        }

    @classmethod
    def from_dict(cls, data: dict) -> League:
        league = cls(
            name=data["name"],
            num_teams=data["num_teams"],
            user_team_number=data["user_team_number"],
            scoring_format=data["scoring_format"],
            roster_slots=data["roster_slots"],
        )
        league.teams = [Team.from_dict(t) for t in data.get("teams", [])]
        league.players_pool = [Player.from_dict(p) for p in data.get("players_pool", [])]
        league.draft_log = [Pick.from_dict(p) for p in data.get("draft_log", [])]
        league.current_round = data.get("current_round", 1)
        league.current_pick_in_round = data.get("current_pick_in_round", 1)
        league.overall_pick = data.get("overall_pick", 1)
        league.is_active = data.get("is_active", True)
        league.completed = data.get("completed", False)
        league.schedule = data.get("schedule", {})
        league.current_week = data.get("current_week", 1)
        league.week_opponent = data.get("week_opponent")
        league.matchup_results = data.get("matchup_results", {})
        league.waiver_log = data.get("waiver_log", [])
        return league
