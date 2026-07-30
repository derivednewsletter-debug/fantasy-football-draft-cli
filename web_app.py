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

    matrix = build_draft_matrix(league)

    return render_template(
        "standings.html",
        league=league,
        matrix=matrix,
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
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"  🏆 Fantasy Football Draft Commander")
    print(f"  🌐 http://localhost:{port}")
    print()
    app.run(host="127.0.0.1", port=port, debug=True)
