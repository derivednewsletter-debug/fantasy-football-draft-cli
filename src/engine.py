"""Draft engine — snake order, VBD, recommendations."""

from __future__ import annotations

import math
from typing import Optional

from src.models import League, Player, ROSTER_PRESETS

# Lazy import for AI advisor (it has a heavy openai dependency)
_ai_advisor = None


def _get_ai_advisor():
    global _ai_advisor
    if _ai_advisor is None:
        try:
            from src.ai_advisor import get_ai_recommendations, is_available
            _ai_advisor = (get_ai_recommendations, is_available)
        except ImportError:
            _ai_advisor = (None, lambda: False)
    return _ai_advisor


# ---------------------------------------------------------------------------
# Snake-order helpers
# ---------------------------------------------------------------------------

def picks_before_next_user_turn(league: League) -> int:
    """How many picks occur before the user picks again (snake)."""
    n = league.num_teams
    r = league.current_round
    p = league.current_pick_in_round  # 1-indexed

    # current team on clock
    if r % 2 == 1:
        current_team = p
    else:
        current_team = n - p + 1

    user = league.user_team_number

    # If user is on the clock now, 0 picks before next turn
    if current_team == user:
        return 0

    # Count how many picks remain in this round
    picks_left_in_round = n - p + 1

    # Work through current round
    for i in range(p, n + 1):
        if r % 2 == 1:
            ct = i
        else:
            ct = n - i + 1
        if ct == user:
            return i - p

    # User not found this round → next round (snake reverses)
    if r % 2 == 1:
        # Odd → next is even: order is N → 1, so user at position N - user + 1
        return picks_left_in_round + (n - user)
    else:
        # Even → next is odd: order is 1 → N, so user at position user
        return picks_left_in_round + (user - 1)


# ---------------------------------------------------------------------------
# Value-Based Drafting (VBD)
# ---------------------------------------------------------------------------

def get_positional_baselines(league: League) -> dict[str, float]:
    """Compute the baseline (replacement-level) projected points per position."""
    position_pools: dict[str, list[float]] = {}
    for p in league.available_players:
        if p.position not in position_pools:
            position_pools[p.position] = []
        position_pools[p.position].append(p.projected_points)

    baselines: dict[str, float] = {}
    for pos, pts in position_pools.items():
        pts.sort(reverse=True)
        # Baseline: the projected points of the last starter at this position
        # Based on league size * typical starters
        starter_count = league.roster_slots.get(pos, 1) * league.num_teams
        if pts:
            idx = min(starter_count, len(pts)) - 1
            baselines[pos] = pts[idx]
        else:
            baselines[pos] = 0.0
    return baselines


def compute_vbd(league: League) -> list[tuple[Player, float, float]]:
    """
    Compute VBD score for each available player.
    Returns list of (Player, vbd_score, turn_loss_probability).
    """
    baselines = get_positional_baselines(league)

    picks_before = picks_before_next_user_turn(league)

    scored: list[tuple[Player, float, float]] = []
    for player in league.available_players:
        baseline = baselines.get(player.position, 0)
        vbd = player.projected_points - baseline
        vbd = max(0.0, vbd)

        # Turn-loss probability: how likely this player is gone by user's next pick
        # Based on ADP relative to pick spacing
        gap = picks_before + 1  # total picks including user's pick
        if player.adp <= league.overall_pick + gap:
            turn_loss = min(0.99, 1.0 - (player.adp - league.overall_pick) / (gap + 10))
            turn_loss = max(0.01, turn_loss)
        else:
            turn_loss = max(0.01, 1.0 - (player.adp - league.overall_pick) / (gap + 20))
            turn_loss = min(0.5, turn_loss)

        scored.append((player, vbd, turn_loss))

    # Sort by VBD descending
    scored.sort(key=lambda x: -x[1])
    return scored


# ---------------------------------------------------------------------------
# Roster gap analysis
# ---------------------------------------------------------------------------

def roster_needs_score(league: League, player: Player) -> float:
    """Score how much a roster position is needed. Higher = more needed."""
    team = league.user_team
    slots = team.roster_slots
    pos = player.position

    needed = 0.0

    # Direct starter need
    if pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        required = slots.get(pos, 0)
        have = team.starter_count_by_position(pos)
        if have < required:
            needed += 3.0 * (required - have)  # big multiplier for unfilled starter

    # Bench / depth need
    total_at_pos = team.roster_count_by_position(pos)
    typical_draft_depth = {
        "QB": 2, "RB": 4, "WR": 5, "TE": 2, "K": 1, "DST": 1,
    }
    if total_at_pos < typical_draft_depth.get(pos, 2):
        needed += 1.0

    # FLEX eligibility bonus
    if pos in ("RB", "WR", "TE"):
        flex_slots = slots.get("FLEX", 0)
        flex_eligible = sum(
            1 for p in team.roster[:team.starting_slots_count]
            if p.position in ("RB", "WR", "TE")
        )
        if flex_eligible < team.roster_slots.get("RB", 2) + team.roster_slots.get("WR", 2) + flex_slots:
            needed += 0.5

    # Special: 2QB/Superflex boosts QB need
    if league.scoring_format == "2QB/Superflex" and pos == "QB":
        needed += 1.5

    return needed


# ---------------------------------------------------------------------------
# Public recommendation API
# ---------------------------------------------------------------------------

def recommend(league: League) -> dict:
    """
    Run the full recommendation engine for the user's team.
    Returns a dict with:
      - safe_picks:    top 3 high-floor, low-risk players
      - upside_picks:  top 3 high-ceiling players
      - sleepers:      top 3 undervalued late-round targets
      - all_ranked:    full VBD-ranked list (for reference)
    """
    scored = compute_vbd(league)

    # Build roster-need-enhanced scores
    enhanced: list[tuple[Player, float, float, float]] = []
    for player, vbd, turn_loss in scored:
        need = roster_needs_score(league, player)
        # Composite: 60% VBD + 40% need
        composite = 0.6 * vbd + 0.4 * need * 10
        enhanced.append((player, vbd, composite, turn_loss))

    # Sort by composite descending
    enhanced.sort(key=lambda x: -x[2])

    all_ranked = [
        {
            "name": p.name,
            "position": p.position,
            "team": p.team,
            "projected_points": p.projected_points,
            "adp": p.adp,
            "vbd": round(vbd, 1),
            "score": round(comp, 1),
            "turn_loss_pct": round(tl * 100, 0),
        }
        for p, vbd, comp, tl in enhanced
    ]

    # Safe picks: high VBD but low turn-loss probability (≤50%)
    safe = [r for r in all_ranked if r["turn_loss_pct"] <= 60][:3]

    # Upside picks: highest projected_points among available (high ceiling)
    upside = sorted(all_ranked, key=lambda r: -r["projected_points"])[:3]

    # Sleepers: lower ADP (late rounds), decent projected_points, low ownership
    late_round_adp_threshold = max(80, league.overall_pick + 30)
    sleepers = [
        r for r in all_ranked
        if r["adp"] >= late_round_adp_threshold
        and r["projected_points"] >= 200
        and r["turn_loss_pct"] <= 40
    ]
    if not sleepers:
        # Fallback: any player with lower-than-average visibility
        sleepers = [r for r in all_ranked if r["adp"] >= 100][:3]
    sleepers = sleepers[:3]

    return {
        "safe_picks": safe,
        "upside_picks": upside,
        "sleepers": sleepers,
        "all_ranked": all_ranked[:20],  # Top 20
        "picks_before_user": picks_before_next_user_turn(league),
    }


# ---------------------------------------------------------------------------
# AI-powered recommendation (NVIDIA Nemotron)
# ---------------------------------------------------------------------------

def recommend_ai(league: League) -> Optional[dict]:
    """
    Run AI-powered draft recommendations via NVIDIA Nemotron.
    Returns enriched recommendations dict with AI analysis, or None if unavailable.
    """
    get_ai_recs, is_avail = _get_ai_advisor()
    if not is_avail():
        return None

    if get_ai_recs is None:
        return None

    ai_result = get_ai_recs(league)
    if ai_result is None:
        return None

    # Enrich with VBD picks so we always have fallback data
    vbd_recs = recommend(league)

    return {
        "ai_analysis": ai_result.get("analysis", ""),
        "ai_top_target": ai_result.get("top_target", {}),
        "ai_safe_picks": ai_result.get("safe_picks", []),
        "ai_upside_picks": ai_result.get("upside_picks", []),
        "ai_sleepers": ai_result.get("sleepers", []),
        "vbd_safe_picks": vbd_recs["safe_picks"],
        "vbd_upside_picks": vbd_recs["upside_picks"],
        "vbd_sleepers": vbd_recs["sleepers"],
        "picks_before_user": vbd_recs["picks_before_user"],
    }


# ---------------------------------------------------------------------------
# Draft board / matrix
# ---------------------------------------------------------------------------

def build_draft_matrix(league: League) -> list[dict]:
    """Build a visual matrix of all teams and their rosters."""
    matrix = []
    for team in league.teams:
        row = {
            "number": team.number,
            "name": team.name,
            "roster": [
                {
                    "name": p.name,
                    "position": p.position,
                    "team": p.team,
                    "projected_points": p.projected_points,
                }
                for p in team.roster
            ],
            "pick_count": len(team.roster),
        }
        matrix.append(row)
    return matrix
