"""NVIDIA Nemotron AI advisor — generates draft recommendations via LLM."""

from __future__ import annotations

import json
import os
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

from src.models import League

# Load .env from project root
load_dotenv()

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"


def is_available() -> bool:
    """Check if NVIDIA API is configured and available."""
    return bool(NVIDIA_API_KEY)


def _build_draft_context(league: League) -> str:
    """Build a concise draft-context string for the LLM prompt."""
    lines = []
    lines.append(f"=== FANTASY FOOTBALL DRAFT STATE ===")
    lines.append(f"Scoring: {league.scoring_format}")
    lines.append(f"Teams: {league.num_teams}")
    lines.append(f"Current Round: {league.current_round}")
    lines.append(f"Overall Pick: #{league.overall_pick}")
    lines.append(f"You are Team #{league.user_team_number}")
    lines.append(f"Roster slots: {dict(league.roster_slots)}")
    lines.append("")

    # Your roster
    user_team = league.user_team
    lines.append("=== YOUR ROSTER ===")
    if user_team.roster:
        for p in user_team.roster:
            lines.append(f"  {p.position} {p.name} ({p.team}) — Proj: {p.projected_points:.0f}")
    else:
        lines.append("  (empty)")
    lines.append("")

    # All drafted players overview
    positions_drafted = {}
    for pick in league.draft_log:
        pos = pick.player_position
        if pos not in positions_drafted:
            positions_drafted[pos] = 0
        positions_drafted[pos] += 1

    lines.append("=== DRAFT PROGRESS ===")
    lines.append(f"Total picks made: {len(league.draft_log)}")
    for pos, count in sorted(positions_drafted.items()):
        lines.append(f"  {pos}: {count} taken")
    lines.append("")

    # Top available players by position (top 8 each)
    lines.append("=== TOP AVAILABLE PLAYERS ===")
    for pos in ["QB", "RB", "WR", "TE", "K", "DST"]:
        avail = [p for p in league.available_players if p.position == pos][:8]
        if avail:
            lines.append(f"  {pos}:")
            for p in avail:
                lines.append(f"    {p.name} ({p.team}) — Proj: {p.projected_points:.0f}, ADP: {p.adp:.1f}, Tier: {p.tier}")
    lines.append("")

    # Roster needs
    lines.append("=== ROSTER NEEDS ===")
    slots = league.roster_slots
    for pos in ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"]:
        need = slots.get(pos, 0)
        have = user_team.roster_count_by_position(pos) if pos != "FLEX" else 0
        if pos == "FLEX":
            flex_eligible = sum(1 for p in user_team.roster if p.position in ("RB", "WR", "TE"))
            lines.append(f"  FLEX: {flex_eligible}/{need} (RB/WR/TE eligible)")
        else:
            lines.append(f"  {pos}: {have}/{need} starters filled")

    return "\n".join(lines)


def get_ai_recommendations(league: League) -> Optional[dict]:
    """
    Call the NVIDIA Nemotron model to generate draft recommendations.
    Returns a dict with safe_picks, upside_picks, sleepers, and analysis,
    or None if the API call fails.
    """
    if not is_available():
        return None

    context = _build_draft_context(league)

    system_prompt = """You are an elite fantasy football draft analyst. Analyze the draft state and recommend picks.

You MUST respond with ONLY valid JSON in this exact format — no other text:
{
  "analysis": "1-2 sentence overview of the draft situation",
  "safe_picks": [
    {"name": "Player Name", "position": "QB", "team": "NFL", "rationale": "Why this is a safe pick"}
  ],
  "upside_picks": [
    {"name": "Player Name", "position": "RB", "team": "NFL", "rationale": "Why this has high upside"}
  ],
  "sleepers": [
    {"name": "Player Name", "position": "WR", "team": "NFL", "rationale": "Why this is a sleeper"}
  ],
  "top_target": {"name": "Player Name", "position": "QB", "team": "NFL", "rationale": "Single best pick right now"}
}

Rules:
- Recommend 3 players in each category (safe, upside, sleepers). If fewer appropriate players exist, list fewer.
- Safe picks = high floor, reliable production, fills a roster need
- Upside picks = high ceiling, breakout potential
- Sleepers = undervalued, late-round value
- Sleepers should have ADP > 80
- Only recommend players that are still available (not on any team's roster)
- Consider roster needs: if you need a QB starter, prioritize QBs
- Factor in snake draft position and how many picks until your next turn"""

    user_prompt = f"Based on the draft state below, recommend my next picks:\n\n{context}"

    try:
        client = OpenAI(
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
        )

        response = client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
            top_p=0.95,
        )

        content = response.choices[0].message.content.strip()

        # Handle potential markdown code fences
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result = json.loads(content)

        # Validate required keys
        required = ["safe_picks", "upside_picks", "sleepers"]
        for key in required:
            if key not in result:
                result[key] = []

        return result

    except Exception as e:
        # Use plain text since we may not have rich Console here
        print(f"  AI advisor note: {e}")
        return None
