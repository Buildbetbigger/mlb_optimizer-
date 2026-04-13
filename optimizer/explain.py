"""Captain classification, stack description, and lineup explanation.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, sections
"Captain Selection Engine" (classify_captain_archetype), "Lineup Output
Requirements", and "Explanation template example".

The explanation layer is not optional - it is what makes the tool usable.
Explanations are assembled from detected reasons and lineup metadata,
never free-written, so output stays consistent and auditable.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

import pandas as pd

from optimizer.conflicts import (
    Conflict,
    detect_conflicts,
    detect_premium_isolation_conflicts,
    merge_conflicts,
)
from optimizer.correlations import (
    compute_lineup_correlation,
    compute_stack_bonus,
    compute_captain_stack_bonus,
)
from optimizer.features import (
    get_stack_teams,
    most_common_team,
    player_ceiling_percentile,
    player_salary_percentile,
)
from optimizer.platform_rules import PlatformConfig
from optimizer.scoring import compute_coherence
from utils.constants import LINEUP_SCORE_WEIGHTS


__all__ = [
    "classify_captain_archetype",
    "describe_stack",
    "build_explanation",
    "build_full_lineup_output",
]


# ---------------------------------------------------------------------------
# Captain archetype classifier (blueprint priority order)
# ---------------------------------------------------------------------------

def classify_captain_archetype(player: Any, df: pd.DataFrame, platform: PlatformConfig) -> str:
    """Return the primary captain archetype for ``player``.

    Priority order (V1, single archetype only):
        1. ace suppression captain  (any pitcher)
        2. premium slugger          (high ceiling and high salary)
        3. top-order volume bat
        4. wraparound salary-release captain
        5. efficient mid-tier bat
        6. script-dependent contrarian (fallback)
    """
    if getattr(player, "is_pitcher", False):
        return "ace suppression captain"

    salary_pctile = player_salary_percentile(player, df)
    ceiling_pctile = player_ceiling_percentile(player, df)
    batting_order = int(getattr(player, "batting_order", 0))

    if ceiling_pctile >= 0.85 and salary_pctile >= 0.80:
        return "premium slugger"

    if batting_order in (1, 2) and ceiling_pctile >= 0.60:
        return "top-order volume bat"

    if batting_order >= 7 and salary_pctile <= 0.30:
        return "wraparound salary-release captain"

    if ceiling_pctile >= 0.50 and salary_pctile <= 0.50:
        return "efficient mid-tier bat"

    return "script-dependent contrarian"


# ---------------------------------------------------------------------------
# Stack description
# ---------------------------------------------------------------------------

def _describe_adjacency(orders: list[int]) -> str:
    """Return a compact run-description like "1-2-3" or "1-2-3 + 5"."""
    if not orders:
        return ""
    sorted_orders = sorted(set(orders))
    runs: list[list[int]] = [[sorted_orders[0]]]
    for o in sorted_orders[1:]:
        if o == runs[-1][-1] + 1:
            runs[-1].append(o)
        else:
            runs.append([o])

    parts = []
    for run in runs:
        if len(run) == 1:
            parts.append(str(run[0]))
        else:
            parts.append("-".join(str(x) for x in run))
    return " + ".join(parts)


def describe_stack(lineup_players: list[Any], captain: Any) -> str:
    """Plain-English description of the lineup's stack shape.

    Examples:
        "4-man LAD stack with 1-2-3-4 adjacency"
        "3-2 LAD/TEX split with adjacency 1-2-3 (LAD)"
        "Balanced 3-3 LAD/TEX split"
    """
    hitters = [p for p in lineup_players if getattr(p, "is_hitter", False)]
    if not hitters:
        return "No hitters in lineup"

    counts = Counter(getattr(p, "team", "") for p in hitters)
    ranked = counts.most_common()
    primary_team, primary_n = ranked[0]
    primary_orders = sorted(
        getattr(p, "batting_order", 0)
        for p in hitters
        if getattr(p, "team", "") == primary_team
        and getattr(p, "batting_order", 0) > 0
    )
    primary_adj = _describe_adjacency(primary_orders)

    if len(ranked) == 1 or ranked[1][1] < 2:
        # Single primary stack (the "secondary" requires >= 2 hitters).
        if primary_adj:
            return f"{primary_n}-man {primary_team} stack with {primary_adj} adjacency"
        return f"{primary_n}-man {primary_team} stack"

    secondary_team, secondary_n = ranked[1]
    if primary_n == secondary_n:
        # Balanced split, e.g. 3-3.
        if primary_adj:
            return (
                f"Balanced {primary_n}-{secondary_n} {primary_team}/{secondary_team} "
                f"split (adjacency {primary_adj} on {primary_team})"
            )
        return f"Balanced {primary_n}-{secondary_n} {primary_team}/{secondary_team} split"

    if primary_adj:
        return (
            f"{primary_n}-{secondary_n} {primary_team}/{secondary_team} split "
            f"with adjacency {primary_adj} ({primary_team})"
        )
    return f"{primary_n}-{secondary_n} {primary_team}/{secondary_team} split"


# ---------------------------------------------------------------------------
# Explanation template (blueprint section "Explanation template example")
# ---------------------------------------------------------------------------

def build_explanation(lineup_dict: Mapping[str, Any]) -> list[str]:
    """Return the human-readable explanation block for a lineup.

    Mirrors the blueprint template:
        - stack_description
        - "Captain: {name} — {archetype}"
        - salary-left note when >= 200
        - one line per conflict flag
        - script tag
    """
    lines: list[str] = []
    lines.append(lineup_dict.get("stack_description", ""))
    lines.append(
        f"Captain: {lineup_dict.get('captain_name', '?')} — "
        f"{lineup_dict.get('captain_archetype', 'unclassified')}"
    )

    salary_left = int(lineup_dict.get("salary_left", 0))
    if salary_left >= 200:
        lines.append(f"Leaves ${salary_left} to reduce duplication")

    for flag in lineup_dict.get("conflict_flags", []) or []:
        lines.append(f"Conflict: {flag}")

    lines.append(f"Script: {lineup_dict.get('scenario_tag', 'n/a')}")
    return [line for line in lines if line]


# ---------------------------------------------------------------------------
# Full lineup output assembly
# ---------------------------------------------------------------------------

def _player_objs_from_solver_result(
    lineup_dict: Mapping[str, Any], df: pd.DataFrame
) -> tuple[list[Any], Any]:
    """Materialize lightweight player objects from a solver result + slate frame.

    Returns (all_players, captain).
    """
    cap_id = lineup_dict["captain_player_id"]
    util_ids = lineup_dict["utility_player_ids"]

    def row_to_obj(pid: str) -> Any:
        row = df[df["player_id"] == pid].iloc[0]
        return type("PlayerView", (), dict(
            player_id=row["player_id"],
            player_name=row["player_name"],
            team=row["team"],
            opp=row["opp"],
            salary=float(row["salary"]),
            batting_order=int(row["batting_order"]),
            is_pitcher=bool(row["is_pitcher"]),
            is_hitter=bool(row["is_hitter"]),
            median_score=float(row["median_score"]),
            ceiling_score=float(row["ceiling_score"]),
            platoon_edge=row.get("platoon_edge", "neutral"),
            over_under=float(row["over_under"]),
        ))()

    captain = row_to_obj(cap_id)
    utility = [row_to_obj(pid) for pid in util_ids]
    return [captain, *utility], captain


def build_full_lineup_output(
    lineup_dict: Mapping[str, Any],
    df: pd.DataFrame,
    platform: PlatformConfig,
    script_name: str,
    preset: Mapping[str, Any],
) -> dict:
    """Assemble the complete display-ready lineup output (Lineup dataclass).

    Inputs:
        lineup_dict  -- result from optimizer.solver.solve_single_lineup
        df           -- scored slate frame (must include median_score etc.)
        platform     -- PlatformConfig
        script_name  -- active scenario name (e.g. "fav_controls")
        preset       -- preset dict (PRESETS["single_entry"] etc.)
    """
    all_players, captain = _player_objs_from_solver_result(lineup_dict, df)
    utility = [p for p in all_players if p is not captain]

    # ---- Classify captain ----
    captain_archetype = classify_captain_archetype(captain, df, platform)

    # ---- Stack description ----
    stack_description = describe_stack(all_players, captain)

    # ---- Conflict detection ----
    primary_stack_team = most_common_team([p for p in all_players if p.is_hitter])
    secondary_stack_team = None
    try:
        # get_stack_teams expects a lineup-like object with all_players
        _, secondary_stack_team = get_stack_teams(
            type("L", (), {"all_players": all_players})()
        )
    except ValueError:
        pass

    # Slate ceiling thresholds (hitter pool)
    hitter_ceilings = df[df["is_hitter"]]["ceiling_score"].astype(float)
    if len(hitter_ceilings):
        slate_ceiling_75 = float(hitter_ceilings.quantile(0.75))
        slate_ceiling_40 = float(hitter_ceilings.quantile(0.40))
    else:
        slate_ceiling_75 = slate_ceiling_40 = 0.0

    lineup_view = type("LineupView", (), dict(
        captain=captain,
        utility=utility,
        all_players=all_players,
        conflicts=[],
    ))()

    primary_conflicts = detect_conflicts(lineup_view, slate_ceiling_75, slate_ceiling_40)
    lineup_view.conflicts = primary_conflicts
    isolation_conflicts = detect_premium_isolation_conflicts(
        lineup_view, df, primary_stack_team, secondary_stack_team
    )
    conflicts = merge_conflicts(primary_conflicts, isolation_conflicts)
    conflict_flags = [c.description for c in conflicts]

    # ---- Coherence (Stage 2) ----
    lineup_view.captain_archetype = captain_archetype
    lineup_view.conflicts = conflicts
    coherence_score = compute_coherence(lineup_view, script_name, preset)

    # ---- Correlation totals (with captain amplification per Approach A here) ----
    cap_idx = 0  # captain is index 0 in all_players
    correlation_score = compute_lineup_correlation(all_players, cap_idx, platform)
    stack_bonus_val = compute_stack_bonus(
        all_players, min_stack_size=int(preset.get("min_stack_size", 3))
    )
    cap_stack_bonus_val = compute_captain_stack_bonus(
        all_players, captain, primary_stack_team
    )

    # ---- Final score (Stage 2 weighted, simple linear combo on raw values) ----
    weights = LINEUP_SCORE_WEIGHTS
    total_w = sum(weights.values())
    norm_w = {k: v / total_w for k, v in weights.items()}
    final_score = (
        norm_w["w_projection"] * float(lineup_dict["median_projection"])
        + norm_w["w_ceiling"] * float(lineup_dict["ceiling_projection"])
        + norm_w["w_correlation"] * (correlation_score + stack_bonus_val + cap_stack_bonus_val)
        + norm_w["w_coherence"] * (100.0 * coherence_score)
        - norm_w["w_conflict"] * sum(
            {"soft": 0.5, "medium": 1.5, "hard": 4.0}[c.severity] for c in conflicts
        )
    )

    out = {
        # core identifiers
        "captain_player_id": lineup_dict["captain_player_id"],
        "captain_name": lineup_dict["captain_name"],
        "captain_team": lineup_dict["captain_team"],
        "captain_archetype": captain_archetype,
        "utility_player_ids": lineup_dict["utility_player_ids"],
        "utility_names": lineup_dict["utility_names"],
        "all_player_ids": lineup_dict["all_player_ids"],
        # salary
        "captain_salary": int(lineup_dict["captain_salary"]),
        "utility_salary": int(lineup_dict["utility_salary"]),
        "total_salary": int(lineup_dict["total_salary"]),
        "salary_left": int(lineup_dict["salary_left"]),
        # scoring
        "median_projection": float(lineup_dict["median_projection"]),
        "ceiling_projection": float(lineup_dict["ceiling_projection"]),
        "correlation_score": float(correlation_score),
        "stack_bonus": float(stack_bonus_val),
        "captain_stack_bonus": float(cap_stack_bonus_val),
        "coherence_score": float(coherence_score),
        "final_score": float(final_score),
        # uniqueness placeholder (Stage 2 will refine using portfolio context)
        "uniqueness_score": 0.0,
        # narrative
        "stack_description": stack_description,
        "scenario_tag": script_name,
        "conflicts": conflicts,
        "conflict_flags": conflict_flags,
    }
    out["explanation"] = build_explanation(out)
    return out
