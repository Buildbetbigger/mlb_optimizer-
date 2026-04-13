"""Stage 1 solver.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, sections
"Solver Design" (context contract, variables, role-dependent captain rule,
pairwise linearization note, Approach A vs B), and "Final Lineup
Objective" (expanded raw lineup_score formula).

Implementation notes:
- V1A uses PuLP. Pairwise correlation bonuses are linearized via standard
  p_ij indicator variables.
- Captain-correlation amplification uses Approach B (deferred): base
  pairwise bonuses enter the Stage 1 objective, and the captain multiplier
  is applied later at Stage 2 via the correlations module.
- The role-dependent captain contribution rule (blueprint) is applied
  directly in the objective:
      median_i * u_i + (median_i * mult) * c_i
      ceiling_boost_i * u_i + (ceiling_boost_i * mult) * c_i
  Stored player scores are NEVER pre-multiplied.
- Conflict penalties are not embedded as soft penalties in the Stage 1
  objective in V1A. Hard constraints that correspond to conflict severity
  (e.g. ``max_opposing_hitters_vs_pitcher``) are expressed as constraints.
  Soft/medium conflict scoring is handled post-solve at Stage 2.
- stack_bonus / captain_stack_bonus use indicator variables gated by a
  minimum-stack constraint, with an estimated per-team average median
  for the point-scale coefficient (V1 approximation).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pulp

from optimizer.platform_rules import PlatformConfig
from utils.constants import (
    ADJACENT_ORDER_BONUS,
    FAVORABLE_PLATOON_BONUS,
    HEART_OF_ORDER_BONUS,
    SAME_TEAM_BONUS,
    TOP_OF_ORDER_BONUS,
    WRAPAROUND_BONUS,
    PITCHER_CONFLICT_PENALTY,
    OPPOSING_CAPTAIN_PENALTY,
)


__all__ = [
    "SolverContext",
    "solve_single_lineup",
    "solve_candidate_set",
]


@dataclass(frozen=True)
class SolverContext:
    """Stage 1 solver inputs.

    Per the blueprint: ``stage2_ranking_weights`` is carried for post-solve
    candidate scoring and is NOT used in the Stage 1 raw objective.
    """
    script_name: str
    preset: str
    platform: str
    stage2_ranking_weights: dict
    script_modifier: dict
    ownership_available: bool


# ---------------------------------------------------------------------------
# Pair-weight helper (percentage weight, not yet point-scale)
# ---------------------------------------------------------------------------

_TOP = {1, 2, 3}
_HEART = {3, 4, 5}


def _hitter_pair_weight(pi: pd.Series, pj: pd.Series) -> float:
    """Sum of applicable pairwise percentage weights for a hitter/hitter pair.

    Returns 0.0 for cross-team pairs.
    """
    if not (pi["is_hitter"] and pj["is_hitter"]):
        return 0.0
    if pi["team"] != pj["team"]:
        return 0.0

    w = SAME_TEAM_BONUS
    oi, oj = int(pi["batting_order"]), int(pj["batting_order"])
    if oi > 0 and oj > 0 and abs(oi - oj) == 1:
        w += ADJACENT_ORDER_BONUS
    if oi in _TOP and oj in _TOP:
        w += TOP_OF_ORDER_BONUS
    if oi in _HEART and oj in _HEART:
        w += HEART_OF_ORDER_BONUS
    if {oi, oj} in ({8, 1}, {9, 1}, {9, 2}):
        w += WRAPAROUND_BONUS

    fav = int(pi.get("platoon_edge", "") == "favorable") + int(
        pj.get("platoon_edge", "") == "favorable"
    )
    if fav:
        w += FAVORABLE_PLATOON_BONUS * fav
    return w


def _pitcher_opposing_weight(pi: pd.Series, pj: pd.Series) -> float:
    """Sum of applicable pairwise weights for a pitcher vs opposing hitter.

    Returns 0.0 when players aren't on opposing teams. Does not attempt
    captain amplification (Approach B).
    """
    if pi["is_pitcher"] and pj["is_hitter"] and pj["team"] == pi["opp"]:
        return PITCHER_CONFLICT_PENALTY
    if pj["is_pitcher"] and pi["is_hitter"] and pi["team"] == pj["opp"]:
        return PITCHER_CONFLICT_PENALTY
    return 0.0


def _pair_base_points(pi: pd.Series, pj: pd.Series) -> float:
    return 0.5 * (float(pi["median_score"]) + float(pj["median_score"]))


# ---------------------------------------------------------------------------
# Single-lineup solver
# ---------------------------------------------------------------------------

def solve_single_lineup(
    df: pd.DataFrame,
    platform: PlatformConfig,
    preset: Mapping[str, Any],
    solver_context: SolverContext,
    locked_captain: str | None = None,
    locked_players: Sequence[str] = (),
    excluded_players: Sequence[str] = (),
    prior_lineups: Sequence[Sequence[str]] = (),
    uniqueness_min: int | None = None,
) -> dict | None:
    """Solve one Stage 1 lineup via PuLP.

    ``df`` must already contain median_score, ceiling_score, ceiling_boost
    columns (from ``compute_all_scores``).

    ``locked_players`` / ``excluded_players`` are lists of player_ids.
    ``prior_lineups`` is a list of player_id lists (one per prior lineup)
    for uniqueness constraints.

    Returns a dict with the lineup structure, or None on infeasibility.
    """
    if uniqueness_min is None:
        uniqueness_min = int(preset.get("uniqueness_min", 2))

    n = len(df)
    roster_size = int(platform.roster_size)
    cap = int(platform.salary_cap)
    cap_mult_salary = float(platform.multiplier_salary_factor)
    cap_mult_points = float(platform.multiplier_points_factor)
    min_spend = int(preset.get("min_salary_spend", 0))
    min_stack_size = int(preset.get("min_stack_size", 3))
    allow_double_pitcher = bool(preset.get("allow_double_pitcher", False))
    max_opp_hitters_vs_pitcher = int(
        preset.get("max_opposing_hitters_vs_pitcher", 3)
    )

    ids = df["player_id"].tolist()
    id_to_idx = {pid: i for i, pid in enumerate(ids)}
    teams = df["team"].tolist()
    salaries = df["salary"].astype(float).tolist()
    is_pitcher = df["is_pitcher"].tolist()
    is_hitter = df["is_hitter"].tolist()
    med = df["median_score"].astype(float).tolist()
    boost = df["ceiling_boost"].astype(float).tolist()

    prob = pulp.LpProblem(
        f"showdown_stage1_{solver_context.script_name}", pulp.LpMaximize
    )
    c = [pulp.LpVariable(f"c_{i}", cat="Binary") for i in range(n)]
    u = [pulp.LpVariable(f"u_{i}", cat="Binary") for i in range(n)]

    # s_i = c_i + u_i is an expression, not a variable.
    def s(i):
        return c[i] + u[i]

    # --- Core constraints ---------------------------------------------------
    prob += pulp.lpSum(c) == 1, "exactly_one_captain"
    prob += pulp.lpSum(u) == roster_size - 1, "exactly_five_utility"
    for i in range(n):
        prob += c[i] + u[i] <= 1, f"no_dup_{i}"

    # Salary cap (captain salary = base * multiplier_salary_factor)
    prob += (
        pulp.lpSum(salaries[i] * cap_mult_salary * c[i] + salaries[i] * u[i] for i in range(n))
        <= cap,
        "salary_cap",
    )
    if min_spend > 0:
        prob += (
            pulp.lpSum(
                salaries[i] * cap_mult_salary * c[i] + salaries[i] * u[i]
                for i in range(n)
            )
            >= min_spend,
            "min_salary_spend",
        )

    # Team presence: require at least one player per distinct team in the pool.
    distinct_teams = sorted(set(teams))
    if platform.requires_one_per_team:
        for t in distinct_teams:
            prob += (
                pulp.lpSum(s(i) for i in range(n) if teams[i] == t) >= 1,
                f"team_presence_{t}",
            )

    # Stack constraint: at least one team has >= min_stack_size hitters.
    # Use a per-team indicator z_t and z_any = max over z_t.
    z_stack = {}
    for t in distinct_teams:
        z = pulp.LpVariable(f"z_stack_{t}", cat="Binary")
        prob += (
            pulp.lpSum(s(i) for i in range(n) if is_hitter[i] and teams[i] == t)
            >= min_stack_size * z,
            f"stack_lower_{t}",
        )
        z_stack[t] = z
    prob += pulp.lpSum(z_stack.values()) >= 1, "at_least_one_valid_stack"

    # Preset: double-pitcher toggle
    if not allow_double_pitcher:
        prob += (
            pulp.lpSum(s(i) for i in range(n) if is_pitcher[i]) <= 1,
            "no_double_pitcher",
        )

    # Preset: max opposing hitters vs any selected pitcher
    for i in range(n):
        if not is_pitcher[i]:
            continue
        opp_team = df.iloc[i]["opp"]
        opp_hit_idx = [
            j for j in range(n) if is_hitter[j] and teams[j] == opp_team
        ]
        if not opp_hit_idx:
            continue
        prob += (
            pulp.lpSum(s(j) for j in opp_hit_idx)
            <= max_opp_hitters_vs_pitcher + (1 - s(i)) * roster_size,
            f"max_opp_hitters_pitcher_{i}",
        )

    # Locks / excludes
    for pid in excluded_players:
        if pid in id_to_idx:
            i = id_to_idx[pid]
            prob += s(i) == 0, f"excluded_{i}"
    for pid in locked_players:
        if pid in id_to_idx:
            i = id_to_idx[pid]
            prob += s(i) == 1, f"locked_{i}"
    if locked_captain and locked_captain in id_to_idx:
        i = id_to_idx[locked_captain]
        prob += c[i] == 1, f"locked_captain_{i}"

    # Uniqueness vs prior lineups: sum(s_i for i in prior_lineup_k) <= R - umin
    max_overlap = roster_size - int(uniqueness_min)
    for k, prior_ids in enumerate(prior_lineups):
        idxs = [id_to_idx[p] for p in prior_ids if p in id_to_idx]
        if len(idxs) == roster_size:
            prob += (
                pulp.lpSum(s(i) for i in idxs) <= max_overlap,
                f"uniq_vs_prior_{k}",
            )

    # --- Pairwise linearization (Approach B: no captain amplification) ------
    pair_vars = {}
    pair_coeffs = {}
    for i, j in combinations(range(n), 2):
        pi, pj = df.iloc[i], df.iloc[j]
        w_hit = _hitter_pair_weight(pi, pj)
        w_pit = _pitcher_opposing_weight(pi, pj)
        base_pts = _pair_base_points(pi, pj)

        total_w = w_hit + w_pit
        coeff = base_pts * total_w
        if abs(coeff) < 1e-12:
            continue

        p_ij = pulp.LpVariable(f"p_{i}_{j}", cat="Binary")
        # p_ij <= s_i, p_ij <= s_j, p_ij >= s_i + s_j - 1
        prob += p_ij <= s(i), f"p_upper_i_{i}_{j}"
        prob += p_ij <= s(j), f"p_upper_j_{i}_{j}"
        prob += p_ij >= s(i) + s(j) - 1, f"p_lower_{i}_{j}"
        pair_vars[(i, j)] = p_ij
        pair_coeffs[(i, j)] = coeff

    # --- Stack / captain-stack bonus indicators -----------------------------
    # Precompute team-level avg hitter median (top min_stack_size)
    team_top_avg = {}
    for t in distinct_teams:
        team_meds = sorted(
            (float(m) for m, h, tm in zip(med, is_hitter, teams) if h and tm == t),
            reverse=True,
        )
        if len(team_meds) >= min_stack_size:
            team_top_avg[t] = sum(team_meds[:min_stack_size]) / min_stack_size
        else:
            team_top_avg[t] = 0.0

    # captain-stack indicator zc_t: 1 iff z_stack_t AND captain is a hitter on team t
    z_cap_stack = {}
    for t in distinct_teams:
        zc = pulp.LpVariable(f"zc_stack_{t}", cat="Binary")
        prob += zc <= z_stack[t], f"zc_gate_stack_{t}"
        prob += (
            zc <= pulp.lpSum(c[i] for i in range(n) if is_hitter[i] and teams[i] == t),
            f"zc_gate_cap_{t}",
        )
        z_cap_stack[t] = zc

    # --- Build the Stage 1 raw objective ------------------------------------
    obj_terms = []

    # Role-dependent player contribution (never pre-multiply stored scores)
    for i in range(n):
        obj_terms.append(med[i] * u[i] + med[i] * cap_mult_points * c[i])
        obj_terms.append(boost[i] * u[i] + boost[i] * cap_mult_points * c[i])

    # Pairwise bonuses (no captain amplification under Approach B)
    for key, coeff in pair_coeffs.items():
        obj_terms.append(coeff * pair_vars[key])

    # Stack + captain-stack one-time bonuses
    for t in distinct_teams:
        obj_terms.append(0.10 * team_top_avg[t] * z_stack[t])
        obj_terms.append(0.08 * team_top_avg[t] * z_cap_stack[t])

    prob += pulp.lpSum(obj_terms)

    # --- Solve --------------------------------------------------------------
    solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)
    if pulp.LpStatus[status] != "Optimal":
        return None

    chosen_cap = [i for i in range(n) if pulp.value(c[i]) > 0.5]
    chosen_util = [i for i in range(n) if pulp.value(u[i]) > 0.5]
    if len(chosen_cap) != 1 or len(chosen_util) != roster_size - 1:
        return None

    cap_idx = chosen_cap[0]
    util_idxs = chosen_util

    cap_row = df.iloc[cap_idx]
    util_rows = df.iloc[util_idxs]

    captain_salary = int(round(float(cap_row["salary"]) * cap_mult_salary))
    utility_salary = int(util_rows["salary"].sum())
    total_salary = captain_salary + utility_salary
    salary_left = cap - total_salary

    captain_projection = float(cap_row["median_score"]) * cap_mult_points
    utility_projection = float(util_rows["median_score"].sum())
    median_projection = captain_projection + utility_projection

    captain_ceiling = float(cap_row["ceiling_score"]) * cap_mult_points
    utility_ceiling = float(util_rows["ceiling_score"].sum())
    ceiling_projection = captain_ceiling + utility_ceiling

    # Stage 1 raw correlation sum (for reporting / Stage 2 consumption).
    stage1_pairwise = sum(
        pair_coeffs[(i, j)] * (1 if pulp.value(pair_vars[(i, j)]) > 0.5 else 0)
        for (i, j) in pair_vars
    )
    stack_bonus_val = sum(
        0.10 * team_top_avg[t] * (1 if pulp.value(z_stack[t]) > 0.5 else 0)
        for t in distinct_teams
    )
    captain_stack_bonus_val = sum(
        0.08 * team_top_avg[t] * (1 if pulp.value(z_cap_stack[t]) > 0.5 else 0)
        for t in distinct_teams
    )

    return {
        "captain_player_id": cap_row["player_id"],
        "captain_name": cap_row["player_name"],
        "captain_team": cap_row["team"],
        "utility_player_ids": util_rows["player_id"].tolist(),
        "utility_names": util_rows["player_name"].tolist(),
        "all_player_ids": [cap_row["player_id"], *util_rows["player_id"].tolist()],
        "captain_idx": cap_idx,
        "utility_idxs": util_idxs,
        "captain_salary": captain_salary,
        "utility_salary": utility_salary,
        "total_salary": total_salary,
        "salary_left": salary_left,
        "median_projection": median_projection,
        "ceiling_projection": ceiling_projection,
        "pairwise_correlation": float(stage1_pairwise),
        "stack_bonus": float(stack_bonus_val),
        "captain_stack_bonus": float(captain_stack_bonus_val),
        "lineup_score_raw": float(pulp.value(prob.objective)),
        "script_name": solver_context.script_name,
    }


# ---------------------------------------------------------------------------
# Candidate-set generation
# ---------------------------------------------------------------------------

def solve_candidate_set(
    df: pd.DataFrame,
    platform: PlatformConfig,
    preset: Mapping[str, Any],
    solver_context: SolverContext,
    num_candidates: int = 20,
    locked_captain: str | None = None,
    locked_players: Sequence[str] = (),
    excluded_players: Sequence[str] = (),
) -> list[dict]:
    """Iteratively generate up to ``num_candidates`` unique lineups."""
    candidates: list[dict] = []
    priors: list[list[str]] = []
    for _ in range(num_candidates):
        lineup = solve_single_lineup(
            df=df,
            platform=platform,
            preset=preset,
            solver_context=solver_context,
            locked_captain=locked_captain,
            locked_players=locked_players,
            excluded_players=excluded_players,
            prior_lineups=priors,
        )
        if lineup is None:
            break
        candidates.append(lineup)
        priors.append(lineup["all_player_ids"])
    return candidates
