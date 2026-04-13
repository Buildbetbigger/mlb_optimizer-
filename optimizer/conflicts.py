"""Conflict taxonomy and detection.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Conflict Taxonomy" (CONFLICT_CODES, detect_conflicts,
detect_premium_isolation_conflicts, has_adjacent_teammate, merge_conflicts,
SOLVER_CONFLICT_PENALTIES).

Conflict-object rules (blueprint):
- player-specific conflicts populate ``player_id`` so they can be
  deduplicated safely by ``(player_id, code)``
- lineup-level conflicts (e.g. ``DOUBLE_PITCHER_SLUGFEST``) leave
  ``player_id = None`` and deduplicate by ``(None, code)``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from optimizer.features import player_salary_percentile
from utils.constants import (
    CONFLICT_CODES,
    SOLVER_CONFLICT_PENALTIES,
)

# Re-export so callers can ``from optimizer.conflicts import CONFLICT_CODES``.
__all__ = [
    "Conflict",
    "CONFLICT_CODES",
    "SOLVER_CONFLICT_PENALTIES",
    "detect_conflicts",
    "detect_premium_isolation_conflicts",
    "has_adjacent_teammate",
    "merge_conflicts",
    "compute_conflict_penalty",
]


_SEVERITY_RANK = {"soft": 1, "medium": 2, "hard": 3}


@dataclass
class Conflict:
    code: str
    severity: str  # "soft", "medium", "hard"
    description: str
    player_id: str | None = None


# ---------------------------------------------------------------------------
# detect_conflicts (blueprint)
# ---------------------------------------------------------------------------

def detect_conflicts(
    lineup: Any, slate_ceiling_75th: float, slate_ceiling_40th: float
) -> list[Conflict]:
    """Detect pitcher-interaction and captain-path conflicts on a lineup.

    Conflict triggers (blueprint):
    - PITCHER_SINGLE_RUNBACK (soft)        -- pitcher vs 1 elite opp hitter
    - PITCHER_SINGLE_RUNBACK_MINOR (soft)  -- pitcher vs 1 non-elite opp hitter
    - PITCHER_TWO_OPP_HITTERS (medium)
    - PITCHER_OPP_MINISTACK (hard)         -- pitcher vs 3+ opposing hitters
    - CAPTAIN_VS_OWN_PITCHER (hard)
    - DOUBLE_PITCHER_SLUGFEST (hard)       -- 2 pitchers, O/U >= 9.0
    - DOUBLE_PITCHER_MODERATE (medium)     -- 2 pitchers, 7.5 <= O/U < 9.0
    - BOTTOM_ORDER_CAPTAIN_NO_PATH (hard)  -- captain order >= 7 below 40th pctile
    """
    conflicts: list[Conflict] = []
    pitchers = [p for p in lineup.all_players if p.is_pitcher]
    captain = lineup.captain

    for pitcher in pitchers:
        opp_hitters = [
            p for p in lineup.all_players
            if p.is_hitter and p.team == pitcher.opp
        ]

        if len(opp_hitters) == 1 and opp_hitters[0].ceiling_score >= slate_ceiling_75th:
            conflicts.append(Conflict(
                code="PITCHER_SINGLE_RUNBACK",
                severity="soft",
                description=f"{pitcher.player_name} vs 1 elite runback {opp_hitters[0].player_name}",
                player_id=opp_hitters[0].player_id,
            ))

        if len(opp_hitters) == 1 and opp_hitters[0].ceiling_score < slate_ceiling_75th:
            conflicts.append(Conflict(
                code="PITCHER_SINGLE_RUNBACK_MINOR",
                severity="soft",
                description=f"{pitcher.player_name} vs 1 non-elite runback {opp_hitters[0].player_name}",
                player_id=opp_hitters[0].player_id,
            ))

        if len(opp_hitters) == 2:
            conflicts.append(Conflict(
                code="PITCHER_TWO_OPP_HITTERS",
                severity="medium",
                description=f"{pitcher.player_name} vs 2 opposing hitters",
            ))

        if len(opp_hitters) >= 3:
            conflicts.append(Conflict(
                code="PITCHER_OPP_MINISTACK",
                severity="hard",
                description=f"{pitcher.player_name} vs {len(opp_hitters)} opposing hitters",
            ))

        if captain.is_hitter and captain.team == pitcher.opp:
            conflicts.append(Conflict(
                code="CAPTAIN_VS_OWN_PITCHER",
                severity="hard",
                description=f"Captain {captain.player_name} opposes {pitcher.player_name}",
                player_id=captain.player_id,
            ))

    if len(pitchers) == 2:
        ou = lineup.all_players[0].over_under
        if ou >= 9.0:
            conflicts.append(Conflict(
                code="DOUBLE_PITCHER_SLUGFEST",
                severity="hard",
                description=f"Double pitcher in {ou} O/U environment",
            ))
        elif ou >= 7.5:
            conflicts.append(Conflict(
                code="DOUBLE_PITCHER_MODERATE",
                severity="medium",
                description=f"Double pitcher in {ou} O/U environment",
            ))

    if captain.is_hitter and captain.batting_order >= 7:
        if captain.ceiling_score < slate_ceiling_40th:
            conflicts.append(Conflict(
                code="BOTTOM_ORDER_CAPTAIN_NO_PATH",
                severity="hard",
                description=f"Captain {captain.player_name} (order {captain.batting_order}) below ceiling threshold",
                player_id=captain.player_id,
            ))

    return conflicts


# ---------------------------------------------------------------------------
# Premium-isolation conflicts (blueprint)
# ---------------------------------------------------------------------------

def has_adjacent_teammate(player: Any, lineup: Any) -> bool:
    """Return True if any other hitter on the player's team has an adjacent
    batting order (|delta| == 1) in this lineup."""
    return any(
        p.is_hitter
        and p.team == player.team
        and p.player_id != player.player_id
        and abs(p.batting_order - player.batting_order) == 1
        for p in lineup.all_players
    )


def detect_premium_isolation_conflicts(
    lineup: Any,
    df: pd.DataFrame,
    primary_stack_team: str,
    secondary_stack_team: str | None = None,
) -> list[Conflict]:
    """Detect off-stack premium hitters with no correlated story.

    Fires:
    - OFFSTACK_PREMIUM_BAT (soft)   -- off-stack hitter at salary pctile >= 0.70
    - UNCORRELATED_PREMIUM (medium) -- off-stack hitter with no adjacent teammate

    Exclusion rule (blueprint):
    - do not fire UNCORRELATED_PREMIUM on a player who already triggers
      PITCHER_SINGLE_RUNBACK or PITCHER_SINGLE_RUNBACK_MINOR in the same
      lineup; a justified one-off runback is already classified by the
      pitcher-conflict rules and should not be double-penalized here.
    """
    # Determine which players were already classified as a single-runback
    # so we can suppress UNCORRELATED_PREMIUM on them.
    already_runback_ids: set[str] = set()
    if hasattr(lineup, "conflicts") and lineup.conflicts:
        for c in lineup.conflicts:
            if c.code in {"PITCHER_SINGLE_RUNBACK", "PITCHER_SINGLE_RUNBACK_MINOR"}:
                if c.player_id:
                    already_runback_ids.add(c.player_id)

    conflicts: list[Conflict] = []

    for p in lineup.all_players:
        if not p.is_hitter:
            continue

        salary_pct = player_salary_percentile(p, df)

        if (
            p.team != primary_stack_team
            and (secondary_stack_team is None or p.team != secondary_stack_team)
            and salary_pct >= 0.70
        ):
            conflicts.append(Conflict(
                code="OFFSTACK_PREMIUM_BAT",
                severity="soft",
                description=f"{p.player_name} is premium but off the primary/secondary stack structure",
                player_id=p.player_id,
            ))

        if (
            p.team != primary_stack_team
            and (secondary_stack_team is None or p.team != secondary_stack_team)
            and not has_adjacent_teammate(p, lineup)
            and p.player_id not in already_runback_ids
        ):
            conflicts.append(Conflict(
                code="UNCORRELATED_PREMIUM",
                severity="medium",
                description=f"{p.player_name} has no correlated teammate in the lineup",
                player_id=p.player_id,
            ))

    return conflicts


# ---------------------------------------------------------------------------
# merge + penalty helpers
# ---------------------------------------------------------------------------

def merge_conflicts(*conflict_lists: Iterable[Conflict]) -> list[Conflict]:
    """Merge multiple conflict lists.

    Rules (blueprint):
    - deduplicate by ``(player_id, code)``
    - if two detectors produce the same code for the same player, keep the
      higher-severity instance
    - sort by severity descending, then by code, so diagnostics and
      explanations remain deterministic
    """
    merged: dict[tuple[str | None, str], Conflict] = {}

    for conflict_list in conflict_lists:
        for c in conflict_list:
            key = (c.player_id, c.code)
            if (
                key not in merged
                or _SEVERITY_RANK[c.severity] > _SEVERITY_RANK[merged[key].severity]
            ):
                merged[key] = c

    return sorted(
        merged.values(),
        key=lambda c: (-_SEVERITY_RANK[c.severity], c.code),
    )


def compute_conflict_penalty(conflicts: list[Conflict]) -> float:
    """Sum the Stage 1 solver penalty contributions of a conflict list."""
    return sum(SOLVER_CONFLICT_PENALTIES[c.severity] for c in conflicts)
