"""Correlation Engine.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Correlation Engine" plus the Solver Design "Pairwise linearization note"
(Approach A vs Approach B for captain-amplification under PuLP).

This module computes point-scale correlation bonuses in *closed form* for
already-assembled lineups, which corresponds to Approach A (full
amplification) - we know the captain identity at evaluation time so the
multiplier can be applied directly. The Stage 1 PuLP solver may instead
use Approach B (deferred amplification) and call back into this module
post-solve.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any, Iterable, Sequence

from optimizer.platform_rules import PlatformConfig
from utils.constants import (
    ADJACENT_ORDER_BONUS,
    FAVORABLE_PLATOON_BONUS,
    HEART_OF_ORDER_BONUS,
    OPPOSING_CAPTAIN_PENALTY,
    PITCHER_CONFLICT_PENALTY,
    SAME_TEAM_BONUS,
    TOP_OF_ORDER_BONUS,
    WRAPAROUND_BONUS,
)


# ---------------------------------------------------------------------------
# Pairwise bonus
# ---------------------------------------------------------------------------

def _is_top_of_order(order: int) -> bool:
    return order in (1, 2, 3)


def _is_heart_of_order(order: int) -> bool:
    return order in (3, 4, 5)


def _is_wraparound(order_a: int, order_b: int) -> bool:
    """8-9-1 wraparound adjacency: one hitter in 8/9 and the other in 1/2."""
    pair = {order_a, order_b}
    return any(
        {a, b} == pair
        for a, b in [(8, 1), (9, 1), (9, 2)]
    )


def compute_pairwise_bonus(
    player_i: Any,
    player_j: Any,
    is_captain_i: bool,
    is_captain_j: bool,
    platform: PlatformConfig,
) -> float:
    """Return the point-scale pairwise bonus for one pair of selected players.

    Conversion rule (blueprint):
        pair_base = 0.5 * (median_i + median_j)
        pair_bonus_points = pair_base * sum(applicable_weights)
        if either is captain:
            pair_bonus_points *= multiplier_points_factor
    """
    pi, pj = player_i, player_j
    pair_base = 0.5 * (float(pi.median_score) + float(pj.median_score))
    weight_sum = 0.0

    # --- Pitcher interactions -------------------------------------------
    if pi.is_pitcher or pj.is_pitcher:
        pitcher = pi if pi.is_pitcher else pj
        other = pj if pi.is_pitcher else pi
        other_is_captain = is_captain_j if pi.is_pitcher else is_captain_i

        if other.is_hitter and other.team == pitcher.opp:
            # Opposing-captain penalty supersedes the standard pitcher
            # conflict penalty - either side being captain triggers it.
            if other_is_captain or (is_captain_i and pi.is_pitcher) or (is_captain_j and pj.is_pitcher):
                weight_sum += OPPOSING_CAPTAIN_PENALTY
            else:
                weight_sum += PITCHER_CONFLICT_PENALTY
        # Pitcher + same-team hitter is handled at the lineup level rather
        # than per-pair to avoid scaling with pair count; return whatever
        # weight we accumulated.
        if weight_sum == 0.0:
            return 0.0
        bonus = pair_base * weight_sum
        if is_captain_i or is_captain_j:
            bonus *= float(platform.multiplier_points_factor)
        return bonus

    # --- Hitter / hitter pair -------------------------------------------
    if not (pi.is_hitter and pj.is_hitter):
        return 0.0

    if pi.team != pj.team:
        return 0.0  # cross-team hitter pairs receive no positive correlation

    weight_sum += SAME_TEAM_BONUS

    oi, oj = int(pi.batting_order), int(pj.batting_order)
    if oi > 0 and oj > 0 and abs(oi - oj) == 1:
        weight_sum += ADJACENT_ORDER_BONUS

    if _is_top_of_order(oi) and _is_top_of_order(oj):
        weight_sum += TOP_OF_ORDER_BONUS

    if _is_heart_of_order(oi) and _is_heart_of_order(oj):
        weight_sum += HEART_OF_ORDER_BONUS

    if _is_wraparound(oi, oj):
        weight_sum += WRAPAROUND_BONUS

    # Favorable-platoon nudge per qualifying member of the pair.
    favorable_count = sum(
        1 for p in (pi, pj) if getattr(p, "platoon_edge", "") == "favorable"
    )
    if favorable_count:
        weight_sum += FAVORABLE_PLATOON_BONUS * favorable_count

    bonus = pair_base * weight_sum
    if is_captain_i or is_captain_j:
        bonus *= float(platform.multiplier_points_factor)
    return bonus


# ---------------------------------------------------------------------------
# Lineup-level aggregations
# ---------------------------------------------------------------------------

def compute_lineup_correlation(
    players: Sequence[Any], captain_idx: int, platform: PlatformConfig
) -> float:
    """Sum every pairwise bonus over the 6-player lineup."""
    total = 0.0
    for i, j in combinations(range(len(players)), 2):
        total += compute_pairwise_bonus(
            players[i],
            players[j],
            is_captain_i=(i == captain_idx),
            is_captain_j=(j == captain_idx),
            platform=platform,
        )
    return total


def _largest_same_team_hitter_group(
    players: Iterable[Any],
) -> tuple[str | None, list[Any]]:
    """Return (team, list_of_hitters) for the largest same-team hitter group."""
    hitters = [p for p in players if p.is_hitter]
    if not hitters:
        return None, []

    counts = Counter(p.team for p in hitters)
    top_team, _ = counts.most_common(1)[0]
    group = [p for p in hitters if p.team == top_team]
    return top_team, group


def compute_stack_bonus(players: Sequence[Any], min_stack_size: int) -> float:
    """One-time lineup-level reward for a valid primary stack shape.

    stack_bonus = avg_median_of_stack_players * 0.10  (when stack >= min)
    """
    _team, group = _largest_same_team_hitter_group(players)
    if len(group) < min_stack_size or not group:
        return 0.0
    avg_med = sum(float(p.median_score) for p in group) / len(group)
    return avg_med * 0.10


def compute_captain_stack_bonus(
    players: Sequence[Any], captain: Any, primary_stack_team: str
) -> float:
    """One-time lineup-level reward for an integrated captain.

    For hitter captains: applies when the captain is a hitter on the
    primary stack team.
    For pitcher captains: applies only when the pitcher pairs with the
    correct same-team offense (i.e. the primary stack is the pitcher's
    own team), so the lineup story remains coherent.
    """
    if captain.is_hitter:
        if captain.team != primary_stack_team:
            return 0.0
    else:
        # Pitcher captain rule
        if captain.team != primary_stack_team:
            return 0.0

    stack_players = [p for p in players if p.is_hitter and p.team == primary_stack_team]
    if not stack_players:
        return 0.0
    avg_med = sum(float(p.median_score) for p in stack_players) / len(stack_players)
    return avg_med * 0.08
