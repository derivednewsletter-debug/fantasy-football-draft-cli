"""Flask web application for the Fantasy Football Draft Commander."""

from __future__ import annotations

import os
import sys
from functools import wraps
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.auth import create_user, verify_user
from src.models import League, ROSTER_PRESETS
from src.storage import (
    save_league,
    load_league,
    list_leagues,
    load_player_data,
    set_user_leagues_dir,
)
from src.engine import recommend, recommend_ai, build_draft_matrix
from src.live_data import (
    get_nfl_state,
    get_live_scores,
    get_scoring_alerts,
    get_player_stats_in_game,
)

app = Flask(__name__)
# Use a fixed secret key from env (for Vercel/session persistence across invocations)
# or generate a random one for local dev
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24).hex()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    """Decorator — redirect unauthenticated users to /login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_email" not in session:
            flash("Please sign in to access that page.", "error")
            return redirect(url_for("login"))
        # Scope storage to this user
        set_user_leagues_dir(session["user_email"])
        return f(*args, **kwargs)
    return decorated


def _get_user_context() -> dict:
    """Return user info dict for template context (empty if not logged in)."""
    email = session.get("user_email")
    if email:
        return {"email": email, "logged_in": True}
    return {"email": None, "logged_in": False}


def _get_league() -> League | None:
    """Load the active league from session."""
    name = session.get("active_league")
    if name:
        league = load_league(name)
        if league:
            return league
    # Try first available league
    leagues = list_leagues()
    if leagues:
        league = load_league(leagues[0]["name"])
        if league:
            session["active_league"] = league.name
            return league
    return None


def _ensure_league():
    """Ensure a league is loaded or redirect."""
    league = _get_league()
    if not league:
        flash("No active league. Create or select one.", "error")
        return None
    return league


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = verify_user(email, password)
        if user:
            session["user_email"] = user["email"]
            set_user_leagues_dir(user["email"])
            flash(f"Welcome back, {user['email']}!", "success")
            return redirect(url_for("draft_room"))
        else:
            flash("Invalid email or password.", "error")

    return render_template("login.html", user=_get_user_context(), active_page=None)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        success, message = create_user(email, password)
        if success:
            flash(message, "success")
            return redirect(url_for("login"))
        else:
            flash(message, "error")

    return render_template("signup.html", user=_get_user_context(), active_page=None)


@app.route("/logout")
def logout():
    session.pop("user_email", None)
    session.pop("active_league", None)
    set_user_leagues_dir(None)
    flash("You've been signed out.", "success")
    return redirect(url_for("login"))


@app.before_request
def _scope_storage():
    """Before every request, scope storage to the logged-in user."""
    if "user_email" in session:
        set_user_leagues_dir(session["user_email"])
    # Allow unauthenticated access to auth pages
    if request.endpoint in ("login", "signup", "static"):
        return


# ---------------------------------------------------------------------------
# Page Routes
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def draft_room():
    league = _ensure_league()
    if not league:
        return redirect(url_for("leagues"))

    vbd_recs = recommend(league)

    return render_template(
        "draft_room.html",
        league=league,
        recs=vbd_recs,
        vbd_recs=vbd_recs,
        ai_recs=None,
        active_page="draft",
        auto_refresh=25 if not league.is_user_on_clock else None,
        user=_get_user_context(),
    )


@app.route("/my-team")
@login_required
def my_team():
    league = _ensure_league()
    if not league:
        return redirect(url_for("leagues"))
    return render_template("my_team.html", league=league, active_page="team", user=_get_user_context())


@app.route("/leagues")
@login_required
def leagues():
    saved = list_leagues()
    league = _get_league()
    return render_template("leagues.html", saved_leagues=saved, league=league, active_page="leagues", user=_get_user_context())


@app.route("/leagues/create", methods=["GET", "POST"])
@login_required
def create_league():
    if request.method == "POST":
        name = request.form.get("name", "Home League")
        num_teams = int(request.form.get("num_teams", 12))
        user_pick = int(request.form.get("user_pick", 1))
        scoring_format = request.form.get("scoring_format", "PPR")

        roster_slots = dict(ROSTER_PRESETS.get(scoring_format, ROSTER_PRESETS["PPR"]))

        # Load player data
        try:
            players_pool = load_player_data()
        except FileNotFoundError:
            flash("Player data file not found!", "error")
            return redirect(url_for("create_league"))

        league = League(
            name=name,
            num_teams=num_teams,
            user_team_number=user_pick,
            scoring_format=scoring_format,
            roster_slots=roster_slots,
            players_pool=players_pool,
        )
        save_league(league)
        session["active_league"] = league.name
        flash(f"League '{name}' created! Welcome to the draft.", "success")
        return redirect(url_for("draft_room"))

    league = _get_league()
    return render_template("create_league.html", league=league, active_page="leagues", user=_get_user_context())


@app.route("/standings")
@login_required
def standings():
    league = _ensure_league()
    if not league:
        return redirect(url_for("leagues"))

    standings_data = compute_standings(league)
    matrix = build_draft_matrix(league)
    week_count = len(league.matchup_results or {})

    # Find user's rank
    user_rank = None
    for s in standings_data:
        if s["team_num"] == league.user_team_number:
            user_rank = s["rank"]
            break

    return render_template(
        "standings.html",
        league=league,
        standings=standings_data,
        matrix=matrix,
        week_count=week_count,
        user_rank=user_rank,
        active_page="standings",
        user=_get_user_context(),
    )


@app.route("/switch/<league_name>")
@login_required
def switch_league(league_name):
    """Switch the active league."""
    league = load_league(league_name)
    if league:
        session["active_league"] = league.name
        flash(f"Switched to '{league.name}'.", "success")
    else:
        flash(f"League '{league_name}' not found.", "error")
    return redirect(url_for("draft_room"))


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.route("/api/draft", methods=["POST"])
@login_required
def api_draft():
    """Draft a player for the team on the clock."""
    league = _get_league()
    if not league:
        flash("No active league.", "error")
        return redirect(url_for("leagues"))

    player_name = request.form.get("player_name", "").strip()
    if not player_name:
        flash("Enter a player name.", "error")
        return redirect(url_for("draft_room"))

    pick = league.record_pick(player_name)
    if pick:
        save_league(league)
        flash(f"✓ Round {pick.round_number}, Pick #{pick.overall_pick}: {pick.player_name} ({pick.player_position}) → Team {pick.team_number}", "success")
    else:
        flash(f"Could not find '{player_name}'. Try a different spelling.", "error")

    # Check draft completion
    if league.overall_pick > sum(league.roster_slots.values()) * league.num_teams:
        league.is_active = False
        league.completed = True
        save_league(league)
        flash("🎉 DRAFT COMPLETE! All roster slots filled.", "success")

    return redirect(url_for("draft_room"))


@app.route("/api/undo")
@login_required
def api_undo():
    """Undo the last pick."""
    league = _get_league()
    if not league:
        return redirect(url_for("leagues"))

    if league.undo_last_pick():
        save_league(league)
        flash("↩ Last pick undone.", "success")
    else:
        flash("Nothing to undo.", "error")

    return redirect(url_for("draft_room"))


@app.route("/api/recommend")
@login_required
def api_recommend():
    """View full VBD recommendations."""
    league = _get_league()
    if not league:
        return redirect(url_for("leagues"))

    recs = recommend(league)
    return render_template("recommendations.html", league=league, recs=recs, ai_recs=None, active_page="draft", user=_get_user_context())


@app.route("/api/ai-recommend")
@login_required
def api_ai_recommend():
    """View full AI recommendations."""
    league = _get_league()
    if not league:
        return redirect(url_for("leagues"))

    vbd_recs = recommend(league)
    ai_recs = recommend_ai(league)

    return render_template(
        "recommendations.html",
        league=league,
        recs=vbd_recs,
        ai_recs=ai_recs,
        vbd_recs=vbd_recs,
        active_page="draft",
        user=_get_user_context(),
    )


# ---------------------------------------------------------------------------
# Gameday Routes
# ---------------------------------------------------------------------------

def _compute_matchup_data(league: League) -> dict | None:
    """Build matchup data dict for the gameday view, pulling live data when available."""
    if not league.week_opponent:
        return None

    user_team = league.user_team
    opp_team = league.teams[league.week_opponent - 1]

    # Try to get live scores from ESPN
    live_scores = []
    live_error = False
    alerts = []
    try:
        live_scores = get_live_scores()
        alerts = get_scoring_alerts(live_scores)
    except Exception:
        live_error = True

    # Calculate fantasy points from live data for each team's players
    user_fps: dict[str, float] = {}
    opp_fps: dict[str, float] = {}

    if live_scores:
        all_names = [p.name for p in user_team.roster] + [p.name for p in opp_team.roster]
        for game in live_scores:
            try:
                stats = get_player_stats_in_game(game["id"])
                for s in stats:
                    s_name = s["name"].split(" ")[-1]  # last name match
                    for p in user_team.roster:
                        if p.name.split()[-1].lower() == s_name.lower():
                            user_fps[p.name] = s.get("fantasy_points", 0)
                    for p in opp_team.roster:
                        if p.name.split()[-1].lower() == s_name.lower():
                            opp_fps[p.name] = s.get("fantasy_points", 0)
            except Exception:
                continue

    # Build user performance rows
    user_perf = []
    for p in user_team.roster[:9]:  # starters + top bench
        fp = user_fps.get(p.name, 0)
        user_perf.append({
            "name": p.name,
            "position": p.position,
            "team_abbr": p.team,
            "fantasy_points": fp or round(p.projected_points * 0.6, 1),
            "matchup": f"{p.team} vs TBD",
            "stats_line": "Live stats pending",
            "live": p.name in user_fps,
        })

    opp_perf = []
    for p in opp_team.roster[:9]:
        fp = opp_fps.get(p.name, 0)
        opp_perf.append({
            "name": p.name,
            "position": p.position,
            "team_abbr": p.team,
            "fantasy_points": fp or round(p.projected_points * 0.6, 1),
            "matchup": f"{p.team} vs TBD",
            "stats_line": "",
            "live": p.name in opp_fps,
        })

    user_score = sum(u["fantasy_points"] for u in user_perf)
    opp_score = sum(o["fantasy_points"] for o in opp_perf)
    total = user_score + opp_score
    win_prob = round((user_score / total * 100) if total > 0 else 50)

    # Build other league games
    league_games = []
    for i in range(0, league.num_teams - 1, 2):
        t1 = league.teams[i]
        t2 = league.teams[i + 1] if i + 1 < league.num_teams else league.teams[0]
        if t1.number in (league.user_team_number, league.week_opponent) or \
           t2.number in (league.user_team_number, league.week_opponent):
            continue
        league_games.append({
            "label": "NFL Sunday",
            "live": len(live_scores) > 0,
            "home_name": t1.name,
            "away_name": t2.name,
            "home_score": round(sum(p.projected_points for p in t1.roster) * 0.1, 1),
            "away_score": round(sum(p.projected_points for p in t2.roster) * 0.1, 1),
        })

    return {
        "user_score": round(user_score, 1),
        "opponent_score": round(opp_score, 1),
        "win_prob": win_prob,
        "state": "LIVE" if live_scores else "UPCOMING",
        "games_remaining": len([g for g in live_scores if g["state"] == "LIVE"]),
        "user_performance": user_perf,
        "opponent_performance": opp_perf,
        "league_games": league_games,
        "alerts": alerts,
        "last_update": "just now",
    }


@app.route("/gameday")
@login_required
def gameday():
    league = _ensure_league()
    if not league:
        return redirect(url_for("leagues"))

    matchup_data = _compute_matchup_data(league)
    live_error = matchup_data is None or matchup_data.get("state") == "ERROR"

    return render_template(
        "gameday.html",
        league=league,
        matchup_data=matchup_data,
        opponent_team=league.teams[league.week_opponent - 1] if league.week_opponent else None,
        live_error=live_error,
        active_page="gameday",
        user=_get_user_context(),
    )


@app.route("/gameday/finalize")
@login_required
def gameday_finalize():
    """Save the current week's matchup result to standings."""
    league = _ensure_league()
    if not league:
        return redirect(url_for("leagues"))

    if not league.week_opponent:
        flash("Set a weekly opponent first.", "error")
        return redirect(url_for("gameday_setup"))

    matchup = _compute_matchup_data(league)
    if not matchup:
        flash("Could not compute matchup data.", "error")
        return redirect(url_for("gameday"))

    week_key = str(league.current_week)
    if not league.matchup_results:
        league.matchup_results = {}
    if week_key not in league.matchup_results:
        league.matchup_results[week_key] = {}

    user_score = matchup["user_score"]
    opp_score = matchup["opponent_score"]

    # Save user result
    if user_score > opp_score:
        user_result = "W"
        opp_result = "L"
    elif opp_score > user_score:
        user_result = "L"
        opp_result = "W"
    else:
        user_result = "T"
        opp_result = "T"

    league.matchup_results[week_key][str(league.user_team_number)] = {
        "pf": user_score, "pa": opp_score, "result": user_result
    }
    league.matchup_results[week_key][str(league.week_opponent)] = {
        "pf": opp_score, "pa": user_score, "result": opp_result
    }

    save_league(league)
    flash(f"Week {league.current_week} finalized! {user_score:.1f} - {opp_score:.1f}", "success")
    return redirect(url_for("standings"))


@app.route("/gameday/setup", methods=["GET", "POST"])
@login_required
def gameday_setup():
    league = _ensure_league()
    if not league:
        return redirect(url_for("leagues"))

    if request.method == "POST":
        week = int(request.form.get("week", 1))
        opponent = int(request.form.get("opponent", 0))

        if opponent < 1 or opponent > league.num_teams or opponent == league.user_team_number:
            flash("Invalid opponent selection.", "error")
            return redirect(url_for("gameday_setup"))

        league.current_week = week
        league.week_opponent = opponent
        save_league(league)
        flash(f"Week {week} opponent set to {league.teams[opponent - 1].name}!", "success")
        return redirect(url_for("gameday"))

    return render_template(
        "gameday_setup.html",
        league=league,
        active_page="gameday",
        user=_get_user_context(),
    )


# ---------------------------------------------------------------------------
# Standings computation
# ---------------------------------------------------------------------------

def compute_standings(league: League) -> list[dict]:
    """
    Compute team standings from matchup_results.
    Returns list of dicts sorted by best record (W, then PF tiebreaker).
    """
    results = league.matchup_results or {}
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
            "streak": [],
            "roster_count": len(t.roster),
        }

    for week_str, week_data in results.items():
        for t_num_str, m in week_data.items():
            t_num = int(t_num_str)
            if t_num in team_stats:
                team_stats[t_num]["pf"] += m.get("pf", 0)
                team_stats[t_num]["pa"] += m.get("pa", 0)
                result = m.get("result", "")
                if result == "W":
                    team_stats[t_num]["wins"] += 1
                    team_stats[t_num]["streak"].append("W")
                elif result == "L":
                    team_stats[t_num]["losses"] += 1
                    team_stats[t_num]["streak"].append("L")
                elif result == "T":
                    team_stats[t_num]["ties"] += 1
                    team_stats[t_num]["streak"].append("T")

    # Build standings list
    standings = []
    for n, s in team_stats.items():
        games = s["wins"] + s["losses"] + s["ties"]
        win_pct = round(s["wins"] / games, 3) if games > 0 else 0.0
        # Streak display
        streak_str = ""
        if s["streak"]:
            count = 0
            last = s["streak"][-1]
            for r in reversed(s["streak"]):
                if r == last:
                    count += 1
                else:
                    break
            streak_str = f"{last}{count}"

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
            "streak": streak_str,
            "roster_count": s["roster_count"],
        })

    # Sort: wins desc, then PF desc
    standings.sort(key=lambda x: (-x["wins"], -x["pf"]))

    # Assign rank and check playoff picture
    for i, s in enumerate(standings):
        s["rank"] = i + 1
        s["playoff"] = i < (league.num_teams // 2)  # Top half make playoffs

    return standings


# ---------------------------------------------------------------------------
# Power Rankings
# ---------------------------------------------------------------------------

def compute_power_rankings(league: League) -> list[dict]:
    """
    Compute power rankings using a composite score (0-100) based on:
    - Record (25%): Win % normalized across the league
    - Points For (25%): Raw scoring output
    - Roster Strength (25%): Sum of projected points of rostered players
    - Waiver Activity (25%): Engagement / roster moves normalized
    Returns list of dicts sorted by composite score descending.
    """
    standings_base = compute_standings(league)  # gets W-L, PF, PA, streak

    # Build per-team data
    team_data: dict[int, dict] = {}
    for s in standings_base:
        tn = s["team_num"]
        team_data[tn] = dict(s)  # copy standings fields

    # Add roster strength (sum of projected points)
    max_roster_score = 1.0
    for t in league.teams:
        projected_sum = sum(p.projected_points for p in t.roster)
        team_data[t.number]["roster_score"] = projected_sum
        max_roster_score = max(max_roster_score, projected_sum)

    # Add waiver activity
    max_waivers = 1.0
    for t in league.teams:
        team_data[t.number]["waivers"] = t.waiver_moves
        max_waivers = max(max_waivers, t.waiver_moves)

    # Normalize each factor and compute composite score
    max_wins = max((d["wins"] for d in team_data.values()), default=1)
    max_pf = max((d["pf"] for d in team_data.values()), default=1)

    for d in team_data.values():
        # Normalize each factor 0-100
        win_score = (d["wins"] / max_wins) * 100 if max_wins > 0 else 0
        pf_score = (d["pf"] / max_pf) * 100 if max_pf > 0 else 0
        roster_score = (d["roster_score"] / max_roster_score) * 100 if max_roster_score > 0 else 0
        waiver_score = (d["waivers"] / max_waivers) * 100 if max_waivers > 0 else 50  # default 50 for 0 moves

        # Weighted composite: 25% each
        d["win_score"] = round(win_score, 1)
        d["pf_score"] = round(pf_score, 1)
        d["roster_score_val"] = round(roster_score, 1)
        d["waiver_score"] = round(waiver_score, 1)

        d["composite"] = round((win_score + pf_score + roster_score + waiver_score) / 4, 1)

        # Trend: up/down based on win% vs composite
        d["trend"] = compute_trend(d, standings_base)

    # Sort by composite descending
    rankings = sorted(team_data.values(), key=lambda x: -x["composite"])

    # Assign rank
    for i, r in enumerate(rankings):
        r["rank"] = i + 1
        r["rank_change"] = 0  # no previous data to compare yet
        r["change_text"] = "—"

    return rankings


def compute_trend(team_dict: dict, standings: list[dict]) -> str:
    """Estimate trend: up if win% > median, down if below, stable otherwise."""
    if not standings:
        return "stable"
    win_pcts = [s["win_pct"] for s in standings]
    median_pct = sorted(win_pcts)[len(win_pcts) // 2] if win_pcts else 0
    if team_dict["win_pct"] > median_pct + 0.05:
        return "up"
    elif team_dict["win_pct"] < median_pct - 0.05:
        return "down"
    return "stable"


@app.route("/power-rankings")
@login_required
def power_rankings():
    """Power Rankings page with composite scores."""
    league = _ensure_league()
    if not league:
        return redirect(url_for("leagues"))

    rankings = compute_power_rankings(league)

    return render_template(
        "power_rankings.html",
        league=league,
        rankings=rankings,
        active_page="power_rankings",
        user=_get_user_context(),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"  🏆 Fantasy Football Draft Commander")
    print(f"  🌐 http://localhost:{port}")
    print()
    app.run(host="127.0.0.1", port=port, debug=True)
