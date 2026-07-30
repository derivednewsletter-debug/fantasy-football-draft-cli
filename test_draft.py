#!/usr/bin/env python3
"""Test script — simulate a 12-team snake draft without interactive prompts."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.models import League, ROSTER_PRESETS
from src.storage import load_player_data, save_league, load_league
from src.engine import recommend, picks_before_next_user_turn

PASS = 0
FAIL = 0


def check(condition: bool, message: str):
    global PASS, FAIL
    if condition:
        print(f"  ✓ {message}")
        PASS += 1
    else:
        print(f"  ✗ {message}")
        FAIL += 1


def test_01_create_league():
    print("\n[1/8] Create league with correct configs")
    players = load_player_data()
    league = League(
        name="Test League",
        num_teams=12,
        user_team_number=7,
        scoring_format="PPR",
        roster_slots=dict(ROSTER_PRESETS["PPR"]),
        players_pool=players,
    )
    check(len(league.teams) == 12, "12 teams created")
    check(league.user_team_number == 7, "User is Team 7")
    check(league.roster_slots["QB"] == 1, "1 QB starter slot")
    check(league.roster_slots["RB"] == 2, "2 RB starter slots")
    check(league.roster_slots["FLEX"] == 1, "1 FLEX slot")
    check(len(league.players_pool) > 100, f"Loaded {len(league.players_pool)} players")
    return league


def test_02_snake_order(league: League):
    print("\n[2/8] Snake order math")
    # Round 1, pick 1 → Team 1
    check(league.team_on_clock == 1, "Round 1 pick 1 → Team 1")

    # Round 1, pick 12 → Team 12
    league.current_pick_in_round = 12
    check(league.team_on_clock == 12, "Round 1 pick 12 → Team 12")

    # Round 2, pick 1 → Team 12
    league.current_round = 2
    league.current_pick_in_round = 1
    check(league.team_on_clock == 12, "Round 2 pick 1 → Team 12")

    # Round 2, pick 12 → Team 1
    league.current_pick_in_round = 12
    check(league.team_on_clock == 1, "Round 2 pick 12 → Team 1")

    # Round 3, pick 1 → Team 1
    league.current_round = 3
    league.current_pick_in_round = 1
    check(league.team_on_clock == 1, "Round 3 pick 1 → Team 1")


def test_03_picks_before_user(league: League):
    print("\n[3/8] Picks-before-user calculation")
    league.user_team_number = 7
    league.num_teams = 12

    # User on clock
    league.current_round = 1
    league.current_pick_in_round = 7
    check(picks_before_next_user_turn(league) == 0, "User on clock → 0 picks before next turn")

    # User at pick 4, round 1 → team 4 on clock, user is team 7
    league.current_round = 1
    league.current_pick_in_round = 4
    # Team 4 → user team 7 → picks 4,5,6 = 3 picks before user
    result = picks_before_next_user_turn(league)
    check(result == 3, f"Team 4 on clock, user=7 → {result} picks before turn (expect 3)")

    # User at pick 10, round 1 → team 10 on clock, user is team 7
    # picks left in round: 10,11,12 (3), next round (even, reversed):
    # team 7 at position 12-7+1=6, so 5 picks before user
    # total: 3 + 5 = 8
    league.current_round = 1
    league.current_pick_in_round = 10
    result = picks_before_next_user_turn(league)
    check(result == 8, f"Team 10 on clock, user=7 → {result} picks before turn (expect 8 — snake reversal)")


def test_04_record_pick(league: League):
    print("\n[4/8] Recording picks and fuzzy matching")
    # Reset to start
    league.current_round = 1
    league.current_pick_in_round = 1
    league.overall_pick = 1

    # Record a pick via fuzzy match
    result = league.record_pick("Christian McCaffrey")
    check(result is not None, "Fuzzy match: 'Christian McCaffrey' found")
    if result:
        check(result.team_number == 1, "Pick recorded for Team 1")
        check(result.player_name == "Christian McCaffrey", "Player name correct")
        check(len(league.draft_log) == 1, "Draft log has 1 entry")

    # Next pick should be Team 2
    check(league.overall_pick == 2, "Overall pick advanced to 2")
    check(league.current_pick_in_round == 2, "Pick in round advanced to 2")

    # Record another pick
    result2 = league.record_pick("CeeDee Lamb")
    check(result2 is not None, "Second pick recorded")
    if result2:
        check(result2.team_number == 2, "Second pick for Team 2")

    # Test fuzzy match with a typo
    result3 = league.record_pick("P. Mahomes")
    check(result3 is not None, "Fuzzy match: 'P. Mahomes' → Patrick Mahomes")
    if result3:
        check(result3.player_name == "Patrick Mahomes", f"Matched to '{result3.player_name}'")


def test_05_undo_pick(league: League):
    print("\n[5/8] Undo functionality")
    before_count = len(league.draft_log)
    result = league.undo_last_pick()
    check(result is True, "Undo succeeded")
    check(len(league.draft_log) == before_count - 1, f"Draft log reduced from {before_count} to {before_count - 1}")


def test_06_vbd_and_recommendations(league: League):
    print("\n[6/8] Recommendation engine")
    # User is Team 7 in a 12-team league
    league.user_team_number = 7

    # Draft a few rounds quickly to create a realistic state
    # Simulate 72 picks (6 rounds x 12 teams)
    for _ in range(72):
        avail = league.available_players
        if not avail:
            break
        league.record_pick(avail[0].name)

    recs = recommend(league)
    check("safe_picks" in recs, "Safe picks returned")
    check("upside_picks" in recs, "Upside picks returned")
    check("sleepers" in recs, "Sleepers returned")
    check(len(recs["safe_picks"]) <= 3, f"≤3 safe picks ({len(recs['safe_picks'])})")
    check(len(recs["upside_picks"]) <= 3, f"≤3 upside picks ({len(recs['upside_picks'])})")
    check(len(recs["sleepers"]) <= 3, f"≤3 sleepers ({len(recs['sleepers'])})")

    if recs["all_ranked"]:
        top = recs["all_ranked"][0]
        check("name" in top and "vbd" in top, "Top ranked player has name + VBD score")
        print(f"  Top VBD: {top['name']} ({top['position']}) - VBD: {top['vbd']}")


def test_07_save_and_load(league: League):
    print("\n[7/8] Persistence (save & load)")
    league.name = "Test_Save_League"
    save_league(league)
    loaded = load_league("Test_Save_League")
    check(loaded is not None, "League loaded from disk")
    if loaded:
        check(loaded.name == league.name, "League name matches")
        check(loaded.num_teams == league.num_teams, "Team count matches")
        check(loaded.overall_pick == league.overall_pick, "Overall pick matches")
        check(len(loaded.draft_log) == len(league.draft_log), "Draft log length matches")


def test_08_roster_matrix(league: League):
    print("\n[8/8] Roster matrix building")
    from src.engine import build_draft_matrix
    matrix = build_draft_matrix(league)
    check(len(matrix) == league.num_teams, f"Matrix has {league.num_teams} teams")
    check(sum(t["pick_count"] for t in matrix) == len(league.draft_log), "Total picks in matrix match draft log")


if __name__ == "__main__":
    print("🏆 FANTASY FOOTBALL DRAFT CLI — VALIDATION TESTS")
    print("=" * 55)

    # Initialise the database backend
    from src.database import init_db, db_create_user
    init_db()
    # Create a test user so save_league() / load_league() have a session
    if not db_create_user("test@draft.com", ""):
        pass  # user already exists
    from src.auth import set_current_user
    set_current_user("test@draft.com")

    try:
        league = test_01_create_league()
        test_02_snake_order(league)
        test_03_picks_before_user(league)
        # Reset for clean recording tests
        players = load_player_data()
        league = League(
            name="Validation",
            num_teams=12,
            user_team_number=7,
            scoring_format="PPR",
            roster_slots=dict(ROSTER_PRESETS["PPR"]),
            players_pool=players,
        )
        test_04_record_pick(league)
        test_05_undo_pick(league)
        test_06_vbd_and_recommendations(league)
        test_07_save_and_load(league)
        test_08_roster_matrix(league)
    except Exception as e:
        print(f"\n[bold red]ERROR: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        FAIL += 1

    total = PASS + FAIL
    print(f"\n{'=' * 55}")
    print(f"  RESULTS: {PASS}/{total} passed", end="")
    if FAIL > 0:
        print(f"  [red]{FAIL} FAILED[/red]")
    else:
        print(f"  [green]ALL PASSED[/green] ✓")
    print()
    sys.exit(FAIL)
