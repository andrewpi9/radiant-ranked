"""VALORANT competitive ranks, mapped onto the Elo leaderboard.

Professors are placed into VALORANT's 25 competitive divisions by their
position on the board, reproducing the game's published population
distribution: the top 0.05% are Radiant, the next 0.17% Immortal 3, and so on
down to the bottom 0.57% in Iron 1.

The published shares total 100.02% (rounding in the source), so assignment
normalises by the actual total rather than assuming 100. Cumulative share
crosses 50% inside Gold 2, so Gold still straddles the median professor.

Division colours are Riot's official per-tier values — all three divisions of a
tier share one colour, exactly as in game, and the badge artwork is what
distinguishes them. `scripts/fetch_rank_icons.py` checks these against the live
API and reports drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

IRON = "#868986"
BRONZE = "#a5855d"
SILVER = "#bbc2c2"
GOLD = "#eccf56"
PLATINUM = "#59a9b6"
DIAMOND = "#b489c4"
ASCENDANT = "#6ae2af"
IMMORTAL = "#bb3d65"
RADIANT = "#ffffaa"


@dataclass(frozen=True)
class Tier:
    name: str              # "Gold 2"
    family: str            # "Gold"
    division: Optional[int]  # 1-3, or None for Radiant
    share: float           # percent of the population
    color: str             # Riot's official tier colour
    slug: str              # "gold2"
    api_tier: int          # Riot's competitive tier id


# Ordered best-first. Shares are the published figures verbatim.
TIERS: tuple[Tier, ...] = (
    Tier("Radiant",     "Radiant",   None, 0.05, RADIANT,   "radiant",   27),
    Tier("Immortal 3",  "Immortal",     3, 0.17, IMMORTAL,  "immortal3", 26),
    Tier("Immortal 2",  "Immortal",     2, 0.22, IMMORTAL,  "immortal2", 25),
    Tier("Immortal 1",  "Immortal",     1, 0.98, IMMORTAL,  "immortal1", 24),
    Tier("Ascendant 3", "Ascendant",    3, 1.07, ASCENDANT, "ascendant3", 23),
    Tier("Ascendant 2", "Ascendant",    2, 2.01, ASCENDANT, "ascendant2", 22),
    Tier("Ascendant 1", "Ascendant",    1, 3.24, ASCENDANT, "ascendant1", 21),
    Tier("Diamond 3",   "Diamond",      3, 2.64, DIAMOND,   "diamond3",  20),
    Tier("Diamond 2",   "Diamond",      2, 3.80, DIAMOND,   "diamond2",  19),
    Tier("Diamond 1",   "Diamond",      1, 5.11, DIAMOND,   "diamond1",  18),
    Tier("Platinum 3",  "Platinum",     3, 4.11, PLATINUM,  "platinum3", 17),
    Tier("Platinum 2",  "Platinum",     2, 5.83, PLATINUM,  "platinum2", 16),
    Tier("Platinum 1",  "Platinum",     1, 7.62, PLATINUM,  "platinum1", 15),
    Tier("Gold 3",      "Gold",         3, 5.84, GOLD,      "gold3",     14),
    Tier("Gold 2",      "Gold",         2, 7.58, GOLD,      "gold2",     13),
    Tier("Gold 1",      "Gold",         1, 8.70, GOLD,      "gold1",     12),
    Tier("Silver 3",    "Silver",       3, 5.98, SILVER,    "silver3",   11),
    Tier("Silver 2",    "Silver",       2, 7.06, SILVER,    "silver2",   10),
    Tier("Silver 1",    "Silver",       1, 7.62, SILVER,    "silver1",    9),
    Tier("Bronze 3",    "Bronze",       3, 4.80, BRONZE,    "bronze3",    8),
    Tier("Bronze 2",    "Bronze",       2, 5.75, BRONZE,    "bronze2",    7),
    Tier("Bronze 1",    "Bronze",       1, 5.17, BRONZE,    "bronze1",    6),
    Tier("Iron 3",      "Iron",         3, 2.56, IRON,      "iron3",      5),
    Tier("Iron 2",      "Iron",         2, 1.54, IRON,      "iron2",      4),
    Tier("Iron 1",      "Iron",         1, 0.57, IRON,      "iron1",      3),
)

# The published figures round to 100.02, not 100 — normalise by what they are.
TOTAL_SHARE = sum(t.share for t in TIERS)

FAMILIES: tuple[str, ...] = tuple(dict.fromkeys(t.family for t in TIERS))


def assign(total: int) -> list[int]:
    """Division index for every board position, best-first.

    Returns a list of length ``total`` where element *i* is the index into
    :data:`TIERS` for the professor at 0-based rank *i*.
    """
    if total <= 0:
        return []

    out: list[int] = []
    cumulative = 0.0
    previous = 0

    for index, tier in enumerate(TIERS):
        cumulative += tier.share
        if index == len(TIERS) - 1:
            boundary = total  # last division absorbs the rounding remainder
        else:
            boundary = round(total * cumulative / TOTAL_SHARE)
            # Radiant is the point of the whole project; never let rounding
            # empty the top division on a small board.
            if index == 0:
                boundary = max(boundary, 1)
        boundary = max(boundary, previous)
        out.extend([index] * (boundary - previous))
        previous = boundary

    return out


def summarize(tier_indices: Sequence[int]) -> list[dict]:
    """Per-division counts and realised shares, best-first."""
    total = len(tier_indices)
    counts = [0] * len(TIERS)
    for i in tier_indices:
        counts[i] += 1

    rows = []
    position = 0
    for index, tier in enumerate(TIERS):
        count = counts[index]
        rows.append(
            {
                "name": tier.name,
                "family": tier.family,
                "division": tier.division,
                "slug": tier.slug,
                "color": tier.color,
                "count": count,
                "target_share": tier.share,
                "actual_share": (100.0 * count / total) if total else 0.0,
                "first_rank": position + 1 if count else None,
                "last_rank": position + count if count else None,
            }
        )
        position += count
    return rows


def roll_up(divisions: Sequence[dict]) -> list[dict]:
    """Collapse division rows into one row per tier family, best-first."""
    out: list[dict] = []
    for family in FAMILIES:
        members = [d for d in divisions if d["family"] == family]
        counts = sum(d["count"] for d in members)
        firsts = [d["first_rank"] for d in members if d["first_rank"]]
        lasts = [d["last_rank"] for d in members if d["last_rank"]]
        out.append(
            {
                "name": family,
                "color": members[0]["color"],
                "divisions": len(members),
                "count": counts,
                "target_share": round(sum(d["target_share"] for d in members), 2),
                "actual_share": sum(d["actual_share"] for d in members),
                "first_rank": min(firsts) if firsts else None,
                "last_rank": max(lasts) if lasts else None,
            }
        )
    return out
