"""Playoff simulation engine — run "what if" scenarios on remaining weeks."""

from __future__ import annotations

import copy
import random
from typing import Optional


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------

# Standard fantasy regular season length
DEFAULT_REGULAR_SEASON_WEEKS = 14


def _simulate_standings_from_results(
    league,
    augmented_results: dict[str, dict],
) -> list[dict]:
    """
    Compute standings from augmented matchup results (real + simulated).
    Uses the same logic as compute_standings in web_app.py but works on
    a passed-in results dict rather than league.matchup_results.
    """
    team_stats: dict[int, dict] = {}

    for t in league.teams:
        n = t.number
        team_stats[n] = {
            "team_num": n,
            "team_name": t.name,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "pf": 0.0,
            "pa": 0.0,
            "roster_count": len(t.roster),
        }

    for week_str, week_data in augmented_results.items():
        for t_num_str, m in week_data.items():
            t_num = int(t_num_str)
            if t_num in team_stats:
                team_stats[t_num]["pf"] += m.get("pf", 0)
                team_stats[t_num]["pa"] += m.get("pa", 0)
                result = m.get("result", "")
                if result == "W":
                    team_stats[t_num]["wins"] += 1
                elif result == "L":
                    team_stats[t_num]["losses"] += 1
                elif result == "T":
                    team_stats[t_num]["ties"] += 1

    standings = []
    for n, s in team_stats.items():
        games = s["wins"] + s["losses"] + s["ties"]
        win_pct = round(s["wins"] / games, 3) if games > 0 else 0.0
        standings.append({
            "team_num": n,
            "team_name": s["team_name"],
            "wins": s["wins"],
            "losses": s["losses"],
            "ties": s["ties"],
            "win_pct": win_pct,
            "games": games,
            "pf": round(s["pf"], 1),
            "pa": round(s["pa"], 1),
            "diff": round(s["pf"] - s["pa"], 1),
            "roster_count": s["roster_count"],
        })

    # Sort: wins desc, then PF desc
    standings.sort(key=lambda x: (-x["wins"], -x["pf"]))

    for i, s in enumerate(standings):
        s["rank"] = i + 1
        s["playoff"] = i < (league.num_teams // 2)

    return standings


def _roster_strength_ranking(league) -> list[dict]:
    """
    Rank teams by total projected points of rostered players.
    Used for auto-simulation weighting.
    """
    strengths = []
    for t in league.teams:
        total_proj = sum(p.projected_points for p in t.roster)
        strengths.append({
            "team_num": t.number,
            "team_name": t.name,
            "strength": total_proj,
        })
    strengths.sort(key=lambda x: -x["strength"])
    for i, s in enumerate(strengths):
        s["rank"] = i + 1
    return strengths


def _pair_teams_by_rank(league, current_standings: list[dict]) -> list[tuple[int, int]]:
    """
    Pair teams for a simulated week based on proximity in the standings.
    1 vs 2, 3 vs 4, 5 vs 6, etc.
    Returns list of (team_a, team_b) tuples.
    """
    sorted_by_rank = sorted(current_standings, key=lambda x: x["rank"])
    pairs = []
    i = 0
    while i < len(sorted_by_rank) - 1:
        t1 = sorted_by_rank[i]["team_num"]
        t2 = sorted_by_rank[i + 1]["team_num"]
        pairs.append((t1, t2))
        i += 2
    # If odd number, last team gets a bye (simulated as win against average)
    return pairs


def _determine_winner(t1_num: int, t2_num: int, strength_ranking: list[dict], random_factor: float = 0.15) -> tuple[int, int, float, float]:
    """
    Given two teams and their roster strengths, simulate a winner.
    Returns (winner_num, loser_num, winner_score, loser_score).

    Higher roster strength = higher win probability.
    random_factor adds variance (0.0 = pure strength, 1.0 = pure luck).
    """
    s1 = next(s["strength"] for s in strength_ranking if s["team_num"] == t1_num)
    s2 = next(s["strength"] for s in strength_ranking if s["team_num"] == t2_num)

    # Add randomness
    roll1 = s1 * (1 - random_factor) + random.random() * s1 * random_factor * 2
    roll2 = s2 * (1 - random_factor) + random.random() * s2 * random_factor * 2

    # Simulate scores (scale strength to reasonable fantasy range)
    score1 = round(max(0, roll1 / 20 + random.uniform(60, 100)), 1)
    score2 = round(max(0, roll2 / 20 + random.uniform(60, 100)), 1)

    if score1 > score2:
        return (t1_num, t2_num, score1, score2)
    elif score2 > score1:
        return (t2_num, t1_num, score2, score1)
    else:
        # Tie — small random bump
        score1 += 0.1
        return (t1_num, t2_num, score1, score2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_current_standings(league) -> list[dict]:
    """Get current league standings (importing compute_standings would cause circular import)."""
    return _simulate_standings_from_results(league, league.matchup_results or {})


def get_remaining_weeks(league, total_weeks: int = DEFAULT_REGULAR_SEASON_WEEKS) -> int:
    """Number of weeks remaining in the regular season."""
    completed = len(league.matchup_results or {})
    return max(0, total_weeks - completed)


def simulate_week(
    league,
    week_num: int,
    results: list[dict],
    base_results: Optional[dict[str, dict]] = None,
) -> list[dict]:
    """
    Simulate a single week with given results and return projected standings.
    
    results: list of {team_num, pf, pa, result}
    base_results: if provided, start from these instead of league.matchup_results
    
    Returns standings list (same format as compute_standings).
    """
    augmented = copy.deepcopy(base_results or (league.matchup_results or {}))
    week_key = f"sim_{week_num}"

    if week_key not in augmented:
        augmented[week_key] = {}

    for r in results:
        tn = r["team_num"]
        augmented[week_key][str(tn)] = {
            "pf": r.get("pf", 100),
            "pa": r.get("pa", 100),
            "result": r.get("result", "T"),
        }

    return _simulate_standings_from_results(league, augmented)


def auto_simulate_rest_of_season(
    league,
    num_simulations: int = 100,
    total_weeks: int = DEFAULT_REGULAR_SEASON_WEEKS,
) -> dict:
    """
    Monte Carlo simulation of the rest of the season.
    Runs num_simulations iterations and returns:
    - avg_standings: Most likely final standings (averaged)
    - playoff_odds: % chance each team makes playoffs
    - scenarios: best / worst / most-likely for user's team
    """
    remaining = get_remaining_weeks(league, total_weeks)
    if remaining <= 0:
        return {
            "avg_standings": get_current_standings(league),
            "playoff_odds": {str(t.number): (1.0 if s["playoff"] else 0.0) for t, s in
                             zip(league.teams, get_current_standings(league))},
            "scenarios": {"best": None, "worst": None, "most_likely": None},
            "simulations_ran": 0,
            "remaining_weeks": 0,
            "note": "Regular season complete.",
        }

    strength_ranking = _roster_strength_ranking(league)

    # Track results across simulations
    all_final_standings: list[list[dict]] = []
    playoff_counts: dict[int, int] = {t.number: 0 for t in league.teams}
    user_best_rank = league.num_teams
    user_worst_rank = 1
    user_best_standings = None
    user_worst_standings = None

    base = copy.deepcopy(league.matchup_results or {})

    for _ in range(num_simulations):
        augmented = copy.deepcopy(base)

        for sim_week in range(remaining):
            week_key = f"sim_{league.current_week + sim_week + 1}"
            # Get current projected standings for this simulation run
            proj = _simulate_standings_from_results(league, augmented)
            pairs = _pair_teams_by_rank(league, proj)

            if week_key not in augmented:
                augmented[week_key] = {}

            for t1, t2 in pairs:
                winner, loser, w_score, l_score = _determine_winner(t1, t2, strength_ranking)
                augmented[week_key][str(winner)] = {"pf": w_score, "pa": l_score, "result": "W"}
                augmented[week_key][str(loser)] = {"pf": l_score, "pa": w_score, "result": "L"}

        final = _simulate_standings_from_results(league, augmented)
        all_final_standings.append(final)

        # Track playoff counts
        for s in final:
            if s["playoff"]:
                playoff_counts[s["team_num"]] += 1

        # Track best/worst for user
        user_s = next(s for s in final if s["team_num"] == league.user_team_number)
        if user_s["rank"] < user_best_rank:
            user_best_rank = user_s["rank"]
            user_best_standings = final
        if user_s["rank"] > user_worst_rank:
            user_worst_rank = user_s["rank"]
            user_worst_standings = final

    # Compute average standings
    avg_wins: dict[int, float] = {t.number: 0.0 for t in league.teams}
    avg_pf: dict[int, float] = {t.number: 0.0 for t in league.teams}

    for final in all_final_standings:
        for s in final:
            avg_wins[s["team_num"]] += s["wins"]
            avg_pf[s["team_num"]] += s["pf"]

    avg_list = []
    for t in league.teams:
        n = t.number
        avg_list.append({
            "team_num": n,
            "team_name": t.name,
            "avg_wins": round(avg_wins[n] / num_simulations, 1),
            "avg_pf": round(avg_pf[n] / num_simulations, 1),
        })

    # Sort by avg wins, then avg PF
    avg_list.sort(key=lambda x: (-x["avg_wins"], -x["avg_pf"]))
    for i, a in enumerate(avg_list):
        a["rank"] = i + 1

    # Current standings for reference
    current = get_current_standings(league)

    playoff_odds = {}
    for t in league.teams:
        pct = round((playoff_counts[t.number] / num_simulations) * 100, 1)
        playoff_odds[str(t.number)] = pct

    # Build scenario labels
    user_current = next((s for s in current if s["team_num"] == league.user_team_number), None)

    return {
        "avg_standings": avg_list,
        "playoff_odds": playoff_odds,
        "current_standings": current,
        "scenarios": {
            "best": user_best_standings,
            "worst": user_worst_standings,
            "most_likely": avg_list,
        },
        "user_best_rank": user_best_rank,
        "user_worst_rank": user_worst_rank,
        "simulations_ran": num_simulations,
        "remaining_weeks": remaining,
        "user_current_rank": user_current["rank"] if user_current else None,
    }


def manual_simulate(
    league,
    week_assignments: dict[int, list[dict]],
    total_weeks: int = DEFAULT_REGULAR_SEASON_WEEKS,
) -> list[dict]:
    """
    Manually assign outcomes for specific weeks and get projected standings.
    
    week_assignments: {week_number: [{team_num, pf, pa, result}, ...]}
    
    Returns projected standings list.
    """
    augmented = copy.deepcopy(league.matchup_results or {})

    for week_num, results in week_assignments.items():
        week_key = f"manual_{week_num}"
        if week_key not in augmented:
            augmented[week_key] = {}
        for r in results:
            tn = r["team_num"]
            augmented[week_key][str(tn)] = {
                "pf": r.get("pf", 100),
                "pa": r.get("pa", 100),
                "result": r.get("result", "T"),
            }

    return _simulate_standings_from_results(league, augmented)
