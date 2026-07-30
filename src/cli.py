"""Rich-powered CLI for the Fantasy Football Draft."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import track
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from src.models import League, Player, ROSTER_PRESETS, pos_flex_eligible
from src.storage import save_league, load_league, list_leagues, load_player_data
from src.engine import recommend, recommend_ai, build_draft_matrix

console = Console()


# ---------------------------------------------------------------------------
# Color scheme
# ---------------------------------------------------------------------------

POS_COLORS = {
    "QB": "bold red",
    "RB": "bold green",
    "WR": "bold blue",
    "TE": "bold yellow",
    "K": "bold magenta",
    "DST": "bold cyan",
}

POS_EMOJI = {
    "QB": "🏈",
    "RB": "🏃",
    "WR": "🎯",
    "TE": "🔗",
    "K": "🎯",
    "DST": "🛡️",
}


def pos_style(pos: str) -> str:
    return POS_COLORS.get(pos, "white")


def fmt_player(p: Player) -> Text:
    """Format a player name with position coloring."""
    t = Text()
    t.append(f"{p.name}", style=pos_style(p.position))
    t.append(f" ({p.position} - {p.team})", style="dim white")
    return t


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def show_main_menu() -> str:
    """Display the main menu and return the choice."""
    console.clear()
    title = Text()
    title.append("🏆 ", style="bold yellow")
    title.append("FANTASY FOOTBALL DRAFT COMMANDER", style="bold white")
    title.append(" 🏆", style="bold yellow")

    console.print(Panel(title, box=box.DOUBLE_EDGE, border_style="bright_green", padding=(1, 2)))

    console.print("\n[bold cyan]MAIN MENU[/bold cyan]\n")

    console.print("  [bold white]1.[/bold white]  [green]Create[/green] a New League Draft")
    console.print("  [bold white]2.[/bold white]  [yellow]Load[/yellow] an Existing Active Draft")
    console.print("  [bold white]3.[/bold white]  [blue]View[/blue] League History & Status")
    console.print("  [bold white]4.[/bold white]  [red]Exit[/red]\n")

    return Prompt.ask("[bold cyan]Choose[/bold cyan]", choices=["1", "2", "3", "4"], default="1")


# ---------------------------------------------------------------------------
# Create league wizard
# ---------------------------------------------------------------------------

def create_league_wizard() -> Optional[League]:
    """Interactive wizard to create a new league."""
    console.clear()
    console.print(Panel("[bold yellow]📋 CREATE NEW LEAGUE[/bold yellow]", box=box.DOUBLE_EDGE, border_style="bright_blue"))

    # League name
    name = Prompt.ask("[bold]League Name[/bold]", default="Home League")

    # Number of teams
    while True:
        try:
            num_teams = int(Prompt.ask("[bold]Number of Teams[/bold]", default="12"))
            if 2 <= num_teams <= 32:
                break
            console.print("[red]Teams must be between 2 and 32.[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")

    # User's pick position
    while True:
        try:
            user_pick = int(Prompt.ask(f"[bold]Your Pick Position (1-{num_teams})[/bold]", default="1"))
            if 1 <= user_pick <= num_teams:
                break
            console.print(f"[red]Pick must be between 1 and {num_teams}.[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")

    # Scoring format
    console.print("\n[bold cyan]Scoring Format:[/bold cyan]")
    console.print("  1. PPR")
    console.print("  2. 0.5 PPR (Half-PPR)")
    console.print("  3. Standard (Non-PPR)")
    console.print("  4. 2QB / Superflex")
    fmt_choice = Prompt.ask("[bold]Choose[/bold]", choices=["1", "2", "3", "4"], default="1")
    fmt_map = {"1": "PPR", "2": "0.5_PPR", "3": "Standard", "4": "2QB/Superflex"}
    scoring_format = fmt_map[fmt_choice]

    # Roster construction
    roster = dict(ROSTER_PRESETS.get(scoring_format, ROSTER_PRESETS["PPR"]))
    console.print(f"\n[bold cyan]Roster Construction ({scoring_format}):[/bold cyan]")

    if scoring_format == "2QB/Superflex":
        console.print("  QB: 2, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DST: 1, BENCH: 7")
    else:
        console.print("  QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DST: 1, BENCH: 6")

    custom = Prompt.ask("[dim]Use custom roster?[/dim]", choices=["y", "n"], default="n")
    if custom == "y":
        console.print("\n[bold yellow]Enter counts for each position:[/bold yellow]")
        for pos in ["QB", "RB", "WR", "TE", "FLEX", "K", "DST", "BENCH"]:
            val = int(Prompt.ask(f"  {pos}", default=str(roster.get(pos, 0))))
            roster[pos] = val

    # Load player data
    console.print("\n[bold cyan]Loading player projections...[/bold cyan]")
    try:
        players_pool = load_player_data()
        console.print(f"  [green]✓[/green] Loaded [bold]{len(players_pool)}[/bold] players")
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red] {e}")
        return None

    league = League(
        name=name,
        num_teams=num_teams,
        user_team_number=user_pick,
        scoring_format=scoring_format,
        roster_slots=roster,
        players_pool=players_pool,
    )

    # Save immediately
    save_league(league)
    console.print(f"\n[green]✓[/green] League '[bold]{name}[/bold]' created and saved!")

    return league


# ---------------------------------------------------------------------------
# League selection
# ---------------------------------------------------------------------------

def select_league(prompt_text: str = "Select a league") -> Optional[League]:
    """Let the user pick from saved leagues."""
    leagues = list_leagues()
    if not leagues:
        console.print("[yellow]No saved leagues found.[/yellow]")
        return None

    console.print(f"\n[bold cyan]{prompt_text}:[/bold cyan]\n")
    for idx, lg in enumerate(leagues, 1):
        status = "[green]Active[/green]" if lg["is_active"] else "[dim]Completed[/dim]"
        console.print(f"  {idx}. [bold]{lg['name']}[/bold]  |  {lg['num_teams']} teams  |  {lg['scoring_format']}  |  {status}")

    console.print(f"  {len(leagues) + 1}. [red]Back[/red]\n")

    choice = Prompt.ask("[bold cyan]Choose[/bold cyan]", default="1")
    try:
        idx = int(choice) - 1
        if idx == len(leagues):
            return None  # back
        if 0 <= idx < len(leagues):
            return load_league(leagues[idx]["name"])
    except (ValueError, IndexError):
        pass
    return None


# ---------------------------------------------------------------------------
# Draft board display
# ---------------------------------------------------------------------------

def render_draft_board(league: League) -> Table:
    """Render a full draft matrix table."""
    table = Table(title="📊 DRAFT BOARD", box=box.ROUNDED, border_style="bright_blue", header_style="bold cyan")
    table.add_column("Pick", style="dim", width=5)
    table.add_column("Round", style="dim", width=6)
    table.add_column("Team", style="bold white", width=10)
    table.add_column("Player", width=22)
    table.add_column("Pos", width=5)
    table.add_column("NFL Team", width=5)
    table.add_column("Proj", justify="right", width=6)

    # Show last 20 picks or all
    picks = league.draft_log[-20:] if len(league.draft_log) > 20 else league.draft_log
    for pick in picks:
        pos = pick.player_position
        table.add_row(
            str(pick.overall_pick),
            str(pick.round_number),
            f"Team {pick.team_number}",
            Text(pick.player_name, style=pos_style(pos)),
            Text(pos, style=pos_style(pos)),
            pick.player_team,
            f"{pick.projected_points:.0f}",
        )
    return table


def render_roster_matrix(league: League) -> Table:
    """Render all teams and their rosters."""
    table = Table(title="🏟️ ALL TEAMS ROSTERS", box=box.ROUNDED, border_style="bright_green")
    table.add_column("Team", style="bold white", width=14)

    # Determine max roster size across all teams
    max_roster = max((len(t.roster) for t in league.teams), default=0)

    for i in range(1, max_roster + 1):
        table.add_column(f"Pick {i}", width=20)

    for team in league.teams:
        row = [f"Team {team.number}"]
        for p in team.roster:
            t = Text()
            t.append(f"{p.name}", style=pos_style(p.position))
            t.append(f" ({p.position})", style="dim")
            row.append(t)
        # Pad if needed
        while len(row) - 1 < max_roster:
            row.append(Text("—", style="dim"))
        table.add_row(*row)

    return table


def render_team_roster(team, league: League) -> Table:
    """Render a single team's roster."""
    table = Table(title=f"📋 {team.name}'s Roster", box=box.SIMPLE, border_style="bright_yellow")
    table.add_column("Pos", width=5)
    table.add_column("Player", width=22)
    table.add_column("NFL Team", width=5)
    table.add_column("Proj", justify="right", width=6)

    # Split starters and bench
    starter_count = team.starting_slots_count
    for idx, p in enumerate(team.roster):
        label = "[bold]ST[/bold] " if idx < starter_count else "BN  "
        t = Text()
        t.append(f"{label}{p.name}", style=pos_style(p.position))
        table.add_row(
            Text(p.position, style=pos_style(p.position)),
            t,
            p.team,
            f"{p.projected_points:.0f}",
        )

    if not team.roster:
        table.add_row("—", Text("Empty roster", style="dim"), "—", "—")

    return table


# ---------------------------------------------------------------------------
# Pick banner
# ---------------------------------------------------------------------------

def render_pick_banner(league: League) -> Panel:
    """Show a prominent banner when the user is on the clock."""
    on_clock = league.team_on_clock
    is_user = league.is_user_on_clock
    num_teams = league.num_teams
    remaining = len(league.available_players)

    content = Text()
    content.append(f"\n  Pick #{league.overall_pick}", style="bold white")
    content.append(f"  |  Round {league.current_round}", style="cyan")
    content.append(f"  |  ", style="white")

    if is_user:
        content.append(f"★ YOU'RE ON THE CLOCK! ★", style="bold yellow reverse")
    else:
        content.append(f"Team {on_clock} is picking", style="bold white")

    content.append(f"\n  {remaining} players available", style="dim")

    border_style = "bright_yellow" if is_user else "bright_blue"
    return Panel(content, box=box.DOUBLE_EDGE, border_style=border_style, padding=(1, 2))


# ---------------------------------------------------------------------------
# Recommendations display
# ---------------------------------------------------------------------------

def render_compact_recommendations(recs: dict) -> Panel:
    """Render a compact recommendation panel for auto-display when user is on clock."""
    lines: list[Text] = []

    picks_before = recs["picks_before_user"]
    lines.append(Text(f"  ⏱  {picks_before} pick(s) until your next turn  |  ", style="bold cyan"))

    # Quick picks: safe + upside names
    safe_names = [r["name"] for r in recs["safe_picks"]]
    upside_names = [r["name"] for r in recs["upside_picks"]]

    lines.append(Text("  🛡️  Safe: ", style="bold green") + Text(", ".join(safe_names), style="white"))
    lines.append(Text("  ⚡  Upside: ", style="bold yellow") + Text(", ".join(upside_names), style="white"))

    if recs["sleepers"]:
        sleeper_names = [r["name"] for r in recs["sleepers"]]
        lines.append(Text("  💤  Sleepers: ", style="bold magenta") + Text(", ".join(sleeper_names), style="white"))

    group = Group(*lines)
    return Panel(group, title="[bold cyan]🎯 RECOMMENDATIONS[/bold cyan]", box=box.SIMPLE, border_style="bright_cyan", padding=(0, 1))


# ---------------------------------------------------------------------------
# AI Recommendations display
# ---------------------------------------------------------------------------

def render_ai_recommendations_compact(recs: dict) -> Panel:
    """Render a compact AI recommendations panel for auto-display when user is on clock."""
    lines: list[Text] = []

    if recs.get("ai_analysis"):
        lines.append(Text(f"  {recs['ai_analysis']}", style="bold cyan"))
        lines.append(Text(""))

    if recs.get("ai_top_target"):
        top = recs["ai_top_target"]
        t = Text("  🎯 Top Target: ", style="bold yellow")
        t.append(f"{top.get('name', '')}", style=pos_style(top.get("position", "")))
        t.append(f" ({top.get('position', '')} - {top.get('team', '')})", style="dim")
        lines.append(t)
        if top.get("rationale"):
            lines.append(Text(f"     └─ {top['rationale']}", style="dim italic"))
        lines.append(Text(""))

    safe = recs.get("ai_safe_picks", [])
    if safe:
        safe_names = [f"{p['name']} ({p['position']})" for p in safe]
        lines.append(Text("  🛡️  Safe: ", style="bold green") + Text(", ".join(safe_names), style="white"))

    upside = recs.get("ai_upside_picks", [])
    if upside:
        upside_names = [f"{p['name']} ({p['position']})" for p in upside]
        lines.append(Text("  ⚡  Upside: ", style="bold yellow") + Text(", ".join(upside_names), style="white"))

    sleepers = recs.get("ai_sleepers", [])
    if sleepers:
        sleeper_names = [f"{p['name']} ({p['position']})" for p in sleepers]
        lines.append(Text("  💤  Sleepers: ", style="bold magenta") + Text(", ".join(sleeper_names), style="white"))

    lines.append(Text("  [dim]Type 'ai' for detailed AI analysis[/dim]"))

    group = Group(*lines)
    return Panel(group, title="[bold magenta]🤖 AI ADVISOR (Nemotron)[/bold magenta]", box=box.SIMPLE, border_style="bright_magenta", padding=(0, 1))


def render_ai_recommendations_full(recs: dict) -> Panel:
    """Render the full AI recommendations panel for the 'ai-recommend' command."""
    lines: list[Text] = []

    if recs.get("ai_analysis"):
        lines.append(Text(f"  🧠 Analysis: {recs['ai_analysis']}\n", style="bold cyan"))

    # Top target
    if recs.get("ai_top_target"):
        top = recs["ai_top_target"]
        t = Text("  🎯 TOP TARGET: ", style="bold yellow reverse")
        t.append(f" {top.get('name', '')}", style=pos_style(top.get("position", "")))
        t.append(f" ({top.get('position', '')} - {top.get('team', '')})", style="bold white")
        lines.append(t)
        if top.get("rationale"):
            lines.append(Text(f"     {top['rationale']}", style="dim italic"))
        lines.append(Text(""))

    # Safe picks
    safe = recs.get("ai_safe_picks", [])
    if safe:
        lines.append(Text("  🛡️  TOP SAFE PICKS (AI)\n", style="bold green"))
        for p in safe:
            t = Text(f"     {p['name']}", style=pos_style(p.get("position", "")))
            t.append(f" ({p.get('position', '')} - {p.get('team', '')})", style="dim")
            lines.append(t)
            if p.get("rationale"):
                lines.append(Text(f"       └─ {p['rationale']}", style="dim italic"))
        lines.append(Text(""))

    # Upside picks
    upside = recs.get("ai_upside_picks", [])
    if upside:
        lines.append(Text("  ⚡  TOP UPSIDE PICKS (AI)\n", style="bold yellow"))
        for p in upside:
            t = Text(f"     {p['name']}", style=pos_style(p.get("position", "")))
            t.append(f" ({p.get('position', '')} - {p.get('team', '')})", style="dim")
            lines.append(t)
            if p.get("rationale"):
                lines.append(Text(f"       └─ {p['rationale']}", style="dim italic"))
        lines.append(Text(""))

    # Sleepers
    sleepers = recs.get("ai_sleepers", [])
    if sleepers:
        lines.append(Text("  💤  TOP SLEEPERS (AI)\n", style="bold magenta"))
        for p in sleepers:
            t = Text(f"     {p['name']}", style=pos_style(p.get("position", "")))
            t.append(f" ({p.get('position', '')} - {p.get('team', '')})", style="dim")
            lines.append(t)
            if p.get("rationale"):
                lines.append(Text(f"       └─ {p['rationale']}", style="dim italic"))
        lines.append(Text(""))

    # VBD comparison
    lines.append(Text("  [bold cyan]VBD Comparisons:[/bold cyan]"))
    vbd_safe = recs.get("vbd_safe_picks", [])
    if vbd_safe:
        vbd_names = [f"{r['name']} ({r['position']}) VBD:{r['vbd']}" for r in vbd_safe]
        lines.append(Text("  📊 VBD Safe: ", style="dim") + Text(", ".join(vbd_names), style="dim"))
    vbd_upside = recs.get("vbd_upside_picks", [])
    if vbd_upside:
        vbd_names = [f"{r['name']} ({r['position']})" for r in vbd_upside]
        lines.append(Text("  📊 VBD Upside: ", style="dim") + Text(", ".join(vbd_names), style="dim"))

    group = Group(*lines)
    return Panel(group, title="[bold magenta]🤖 AI DRAFT ADVISOR (Nemotron)[/bold magenta]", box=box.ROUNDED, border_style="bright_magenta", padding=(1, 2))


def render_recommendations(recs: dict) -> Panel:
    """Render the full recommendation panel."""
    lines: list[Text] = []

    # On the clock info
    picks_before = recs["picks_before_user"]
    lines.append(Text(f"  ⏱  {picks_before} pick(s) until your next turn\n", style="bold cyan"))

    # Safe picks
    lines.append(Text("  🛡️  TOP SAFE PICKS\n", style="bold green"))
    for r in recs["safe_picks"]:
        t = Text()
        t.append(f"     {r['name']}", style=pos_style(r["position"]))
        t.append(f" ({r['position']} - {r['team']})", style="dim")
        t.append(f"  Proj: {r['projected_points']:.0f}", style="white")
        t.append(f"  VBD: {r['vbd']}", style="cyan")
        t.append(f"  Gone: {r['turn_loss_pct']:.0f}%", style="yellow")
        lines.append(t)
        # Rationale
        rationale = _rationale_for_pick(r)
        lines.append(Text(f"       └─ {rationale}", style="dim italic"))

    lines.append(Text(""))

    # Upside picks
    lines.append(Text("  ⚡ TOP UPSIDE PICKS\n", style="bold yellow"))
    for r in recs["upside_picks"]:
        t = Text()
        t.append(f"     {r['name']}", style=pos_style(r["position"]))
        t.append(f" ({r['position']} - {r['team']})", style="dim")
        t.append(f"  Proj: {r['projected_points']:.0f}", style="white")
        t.append(f"  ADP: {r['adp']:.0f}", style="cyan")
        lines.append(t)
        rationale = _rationale_for_pick(r)
        lines.append(Text(f"       └─ {rationale}", style="dim italic"))

    lines.append(Text(""))

    # Sleepers
    lines.append(Text("  💤 TOP SLEEPERS\n", style="bold magenta"))
    for r in recs["sleepers"]:
        t = Text()
        t.append(f"     {r['name']}", style=pos_style(r["position"]))
        t.append(f" ({r['position']} - {r['team']})", style="dim")
        t.append(f"  Proj: {r['projected_points']:.0f}", style="white")
        t.append(f"  ADP: {r['adp']:.0f}", style="cyan")
        lines.append(t)
        rationale = _rationale_for_pick(r)
        lines.append(Text(f"       └─ {rationale}", style="dim italic"))

    group = Group(*lines)
    return Panel(group, title="[bold cyan]🎯 RECOMMENDATIONS[/bold cyan]", box=box.ROUNDED, border_style="bright_cyan", padding=(1, 2))


def _rationale_for_pick(r: dict) -> str:
    """Generate a brief rationale string for a pick recommendation."""
    parts = []
    pos = r["position"]
    if pos in ("QB", "RB", "WR", "TE"):
        needed_text = {
            "QB": "fills QB spot",
            "RB": "fills RB spot",
            "WR": "fills WR spot",
            "TE": "fills TE spot",
        }.get(pos, "fills roster")
        parts.append(needed_text)

    turn_loss = r.get("turn_loss_pct", 0)
    if turn_loss > 70:
        parts.append(f"⚠️ {turn_loss:.0f}% gone by next pick")
    elif turn_loss > 40:
        parts.append(f"{turn_loss:.0f}% chance gone by next pick")
    else:
        parts.append(f"likely available next turn ({turn_loss:.0f}% gone)")

    if r.get("vbd", 0) > 20:
        parts.append(f"high VBD ({r['vbd']})")

    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Available players display (filtered)
# ---------------------------------------------------------------------------

def render_available_by_position(league: League, top_n: int = 5) -> Table:
    """Show top available players grouped by position."""
    table = Table(title="📋 TOP AVAILABLE PLAYERS", box=box.SIMPLE, border_style="dim")
    table.add_column("Pos", width=5)
    table.add_column("Player", width=22)
    table.add_column("Proj", justify="right", width=6)
    table.add_column("ADP", justify="right", width=6)
    table.add_column("Tier", width=5)

    for pos in ["QB", "RB", "WR", "TE", "K", "DST"]:
        players = [
            p for p in league.available_players
            if p.position == pos
        ][:top_n]
        for p in players:
            table.add_row(
                Text(pos, style=pos_style(pos)),
                Text(p.name, style=pos_style(pos)),
                f"{p.projected_points:.0f}",
                f"{p.adp:.1f}",
                str(p.tier),
            )

    return table


# ---------------------------------------------------------------------------
# Interactive draft loop
# ---------------------------------------------------------------------------

def draft_loop(league: League) -> None:
    """Main interactive draft loop."""
    while league.is_active and not league.completed:
        console.clear()

        # Pick banner
        console.print(render_pick_banner(league))

        # Auto-display recommendations when user is on the clock
        if league.is_user_on_clock:
            console.print()
            # Try AI first, fall back to VBD
            try:
                ai_recs = recommend_ai(league)
                if ai_recs:
                    console.print(render_ai_recommendations_compact(ai_recs))
                else:
                    recs = recommend(league)
                    console.print(render_compact_recommendations(recs))
            except Exception:
                recs = recommend(league)
                console.print(render_compact_recommendations(recs))
            console.print()

        # Available players quick view
        console.print(render_available_by_position(league, top_n=3))
        console.print()

        # Show recent picks
        if league.draft_log:
            console.print(render_draft_board(league))
            console.print()

        # Prompt
        on_clock = league.team_on_clock
        is_user = league.is_user_on_clock
        prompt_prefix = "[bold yellow]★ PICK[/bold yellow]" if is_user else f"[bold]Team {on_clock}[/bold]"
        prompt_text = f"{prompt_prefix} > "

        user_input = Prompt.ask(prompt_text)

        if not user_input.strip():
            continue

        cmd = user_input.strip()

        # --- Commands ---
        if cmd.lower() == "exit":
            save_league(league)
            console.print("[green]✓[/green] League saved. Returning to main menu.")
            break

        elif cmd.lower() == "save":
            save_league(league)
            console.print("[green]✓[/green] League saved! Press Enter to continue...")
            input()

        elif cmd.lower() == "undo":
            if league.undo_last_pick():
                console.print("[yellow]↩[/yellow] Last pick undone! Press Enter to continue...")
            else:
                console.print("[red]✗[/red] Nothing to undo. Press Enter to continue...")
            input()

        elif cmd.lower() == "board":
            console.clear()
            console.print(render_roster_matrix(league))
            console.print()
            console.print(render_team_roster(league.user_team, league))
            console.print("\n[dim]Press Enter to return to draft...[/dim]")
            input()

        elif cmd.lower() in ("ai", "ai-recommend"):
            console.clear()
            console.print("[bold magenta]🤖 Consulting AI Advisor (Nemotron)...[/bold magenta]")
            ai_recs = recommend_ai(league)
            if ai_recs:
                console.print(render_ai_recommendations_full(ai_recs))
            else:
                console.print("[yellow]⚠ AI advisor unavailable. Set NVIDIA_API_KEY in .env or environment.[/yellow]")
                console.print()
                # Fall back to VBD recommendations
                console.print("[bold cyan]📊 Falling back to VBD recommendations...[/bold cyan]")
                recs = recommend(league)
                console.print(render_recommendations(recs))
            console.print("\n[dim]Press Enter to return to draft...[/dim]")
            input()

        elif cmd.lower() == "recommend":
            console.clear()
            console.print("[bold cyan]🧠 Running recommendation engine...[/bold cyan]")
            recs = recommend(league)
            console.print(render_recommendations(recs))
            console.print("\n[dim]Press Enter to return to draft...[/dim]")
            input()

        elif cmd.lower().startswith("pick "):
            # Explicit pick command
            player_name = cmd[5:].strip()
            result = league.record_pick(player_name)
            if result is None:
                console.print(f"[red]✗[/red] Could not find player '[bold]{player_name}[/bold]'. Try again.")
                input()
        elif cmd.lower().startswith("p "):
            player_name = cmd[2:].strip()
            result = league.record_pick(player_name)
            if result is None:
                console.print(f"[red]✗[/red] Could not find player '[bold]{player_name}[/bold]'. Try again.")
                input()
        else:
            # Assume it's a player name
            result = league.record_pick(cmd)
            if result is None:
                # Could be a number-based format like "12 Patrick Mahomes"
                parts = cmd.split(maxsplit=1)
                if len(parts) == 2 and parts[0].isdigit():
                    player_name = parts[1]
                    result = league.record_pick(player_name)

            if result is None:
                console.print(f"[red]✗[/red] Could not find player '[bold]{cmd}[/bold]'. Type 'recommend' for suggestions.")
                input()

        # Check if draft is complete
        if league.overall_pick > sum(league.roster_slots.values()) * league.num_teams:
            league.is_active = False
            league.completed = True
            save_league(league)
            console.clear()
            console.print(Panel(
                "[bold green]🎉 DRAFT COMPLETE! 🎉[/bold green]",
                border_style="bright_green",
                box=box.DOUBLE_EDGE,
            ))
            console.print(render_roster_matrix(league))
            console.print(f"\n[bold cyan]Your Final Roster:[/bold cyan]")
            console.print(render_team_roster(league.user_team, league))
            console.print("\n[dim]Press Enter to return to main menu...[/dim]")
            input()
            break

    save_league(league)


# ---------------------------------------------------------------------------
# League history / status view
# ---------------------------------------------------------------------------

def show_league_status() -> None:
    """Show saved leagues and allow inspecting their details."""
    league = select_league("View league status")
    if league is None:
        return

    console.clear()
    console.print(Panel(
        f"[bold yellow]{league.name}[/bold yellow]\n"
        f"  Scoring: {league.scoring_format}\n"
        f"  Teams: {league.num_teams} | Your Team: #{league.user_team_number}\n"
        f"  Status: {'[green]Active[/green]' if league.is_active else '[dim]Completed[/dim]'}\n"
        f"  Current Pick: #{league.overall_pick} | Round {league.current_round}\n"
        f"  Players Drafted: {len(league.draft_log)}",
        title="[bold cyan]📊 LEAGUE STATUS[/bold cyan]",
        box=box.DOUBLE_EDGE,
        border_style="bright_cyan",
    ))

    if league.draft_log:
        console.print(render_draft_board(league))

    console.print()
    if league.is_active:
        action = Prompt.ask("[bold cyan]Actions[/bold cyan]", choices=["resume", "delete", "back"], default="resume")
        if action == "resume":
            draft_loop(league)
        elif action == "delete":
            from src.storage import delete_league
            confirm = Prompt.ask("[bold red]Are you sure?[/bold red]", choices=["y", "n"], default="n")
            if confirm == "y":
                delete_league(league.name)
                console.print(f"[red]✗[/red] League '{league.name}' deleted.")
                input("Press Enter to continue...")
    else:
        input("[dim]Press Enter to continue...[/dim]")


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def run() -> None:
    """Main application entry point."""
    while True:
        choice = show_main_menu()

        if choice == "1":
            # Create new league
            league = create_league_wizard()
            if league:
                draft_loop(league)

        elif choice == "2":
            # Load existing draft
            league = select_league("Load active draft")
            if league:
                draft_loop(league)

        elif choice == "3":
            # View league history
            show_league_status()

        elif choice == "4":
            console.print("\n[bold green]🏆 Good luck this season! 🏆[/bold green]")
            break


if __name__ == "__main__":
    run()
