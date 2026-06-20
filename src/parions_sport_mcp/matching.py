"""Fuzzy matching between FDJ odds events and external match results.

Provider team names rarely match exactly ("St Etienne" vs "Saint-Étienne",
"Red Star" vs "Red Star FC"), so linking relies on normalized token overlap and
a character-ratio fallback rather than equality.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from .repository import slugify

# Club/structure suffixes that carry no discriminating signal.
_STOPWORDS = {
    "fc", "sc", "cf", "ac", "as", "afc", "sk", "bk", "if", "cd", "ud", "rc",
    "ca", "us", "cs", "club", "de", "la", "le", "los", "el", "the",
}

# Outcome labels/teams that denote a draw, not a competitor.
_DRAW_TOKENS = {"N", "X"}


def _tokens(name: str | None) -> set[str]:
    return {t for t in slugify(name).split("-") if t and t not in _STOPWORDS}


def team_similarity(a: str | None, b: str | None) -> float:
    """Return a 0..1 similarity between two team names."""

    if not a or not b:
        return 0.0
    slug_a, slug_b = slugify(a), slugify(b)
    if not slug_a or not slug_b:
        return 0.0

    ratio = SequenceMatcher(None, slug_a, slug_b).ratio()
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return ratio

    overlap = tokens_a & tokens_b
    jaccard = len(overlap) / len(tokens_a | tokens_b)
    containment = len(overlap) / min(len(tokens_a), len(tokens_b))
    return max(ratio, jaccard, containment * 0.95)


def fixture_similarity(
    home_a: str | None,
    away_a: str | None,
    home_b: str | None,
    away_b: str | None,
) -> tuple[float, str]:
    """Score two fixtures, trying both home/away orientations.

    Returns ``(score, orientation)`` where orientation is ``"same"`` or
    ``"swapped"`` (the second fixture has home/away reversed relative to the
    first).
    """

    direct = (team_similarity(home_a, home_b) + team_similarity(away_a, away_b)) / 2
    swapped = (team_similarity(home_a, away_b) + team_similarity(away_a, home_b)) / 2
    if swapped > direct:
        return swapped, "swapped"
    return direct, "same"


def extract_teams(event: dict[str, Any]) -> tuple[str | None, str | None]:
    """Pull the two competitors from a repository event dict.

    Prefers a head-to-head market (1/N/2, Face a Face) whose outcomes carry team
    names, falling back to splitting the event label on its separator.
    """

    for market in event.get("markets") or []:
        named = [
            outcome
            for outcome in market.get("outcomes") or []
            if outcome.get("team")
            and (outcome.get("label") or "").strip().upper() not in _DRAW_TOKENS
            and outcome["team"].strip().upper() not in _DRAW_TOKENS
        ]
        if len(named) == 2:
            return named[0]["team"], named[1]["team"]

    name = (event.get("event_name") or "").strip()
    if "-" in name:
        left, _, right = name.partition("-")
        return (left.strip() or None), (right.strip() or None)
    return None, None
