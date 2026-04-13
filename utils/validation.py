"""Validation Rules.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Validation Rules" including the "Solver infeasibility handling"
subsection.

The app should warn clearly rather than fail silently. These helpers
return plain structures (list[str] / dict) so the UI can surface them
without embedding policy.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from optimizer.data_prep import (
    OPPONENT_ALIASES,
    REQUIRED_COLUMNS,
    get_slate_quality,
)
from optimizer.platform_rules import PlatformConfig


# ---------------------------------------------------------------------------
# CSV-level validation
# ---------------------------------------------------------------------------

def validate_csv_columns(df: pd.DataFrame) -> list[str]:
    """Return a list of column-level violations (empty == valid)."""
    errors: list[str] = []

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append("Missing required columns: " + ", ".join(missing))

    if not any(c in df.columns for c in OPPONENT_ALIASES):
        errors.append(
            "Missing opponent column; expected one of: "
            + ", ".join(OPPONENT_ALIASES)
        )

    if df.empty:
        errors.append("CSV has no rows.")

    return errors


def validate_data_types(df: pd.DataFrame) -> list[str]:
    """Check numeric columns parse and batting orders are reasonable."""
    errors: list[str] = []

    numeric_required = [
        "salary",
        "ppg_projection",
        "value_projection",
        "spread",
        "over_under",
        "implied_team_score",
        "L5_fppg_avg",
        "L10_fppg_avg",
        "szn_fppg_avg",
    ]
    for col in numeric_required:
        if col in df.columns:
            parsed = pd.to_numeric(df[col], errors="coerce")
            bad = parsed.isna().sum()
            if bad:
                errors.append(
                    f"Column '{col}' has {bad} non-numeric value(s)"
                )

    # batting order: blank/NaN OK for pitchers; hitters can have 1-9
    if "confirmed_order" in df.columns:
        parsed = pd.to_numeric(df["confirmed_order"], errors="coerce")
        out_of_range = parsed.dropna()
        out_of_range = out_of_range[(out_of_range < 1) | (out_of_range > 9)]
        if len(out_of_range):
            errors.append(
                f"confirmed_order contains {len(out_of_range)} value(s) "
                "outside the 1-9 range"
            )

    # At most one starting pitcher per team
    if "starting_pitcher" in df.columns and "team" in df.columns:
        flags = df["starting_pitcher"].astype(str).str.strip().str.lower()
        is_starter = flags.isin({"true", "yes", "y", "1", "sp", "starter"})
        if is_starter.any():
            per_team = df[is_starter]["team"].value_counts()
            multi = per_team[per_team > 1]
            if len(multi):
                errors.append(
                    "More than one starting pitcher inferred for team(s): "
                    + ", ".join(multi.index.astype(str).tolist())
                )

    return errors


def validate_slate_quality(df: pd.DataFrame) -> dict:
    """Delegate to the slate-quality metadata produced by data_prep.

    Passthrough keeps a single source of truth for slate confidence logic.
    """
    return get_slate_quality(df)


# ---------------------------------------------------------------------------
# Lineup-level validation
# ---------------------------------------------------------------------------

def validate_lineup(
    lineup_players: Sequence[Any],
    captain_idx: int,
    platform: PlatformConfig,
    preset: Mapping[str, Any],
) -> list[str]:
    """Return a list of rule violations (empty == valid).

    Checks:
    - roster size matches platform.roster_size
    - salary within cap, with captain salary = base * multiplier_salary_factor
    - requires_one_per_team (at least one player per team on the slate)
    - no duplication between captain and utility
    - minimum salary spend from preset met
    - captain multiplier applied to scoring fields (if median_score /
      ceiling_score / captain_score exist on the player objects)
    """
    violations: list[str] = []

    if len(lineup_players) != platform.roster_size:
        violations.append(
            f"Roster size is {len(lineup_players)}; expected "
            f"{platform.roster_size}"
        )
    if not (0 <= captain_idx < len(lineup_players)):
        violations.append(
            f"Captain index {captain_idx} is out of range for a lineup of "
            f"{len(lineup_players)} players"
        )
        return violations  # cannot continue structural checks

    captain = lineup_players[captain_idx]
    utility = [p for i, p in enumerate(lineup_players) if i != captain_idx]

    # Duplicate check
    ids = [getattr(p, "player_id", None) for p in lineup_players]
    if len(set(ids)) != len(ids):
        violations.append("Duplicate player detected between captain and utility")

    # Salary totals: captain enters at base_salary * multiplier_salary_factor.
    captain_salary = float(captain.salary) * float(platform.multiplier_salary_factor)
    util_salary = sum(float(p.salary) for p in utility)
    total_salary = captain_salary + util_salary
    if total_salary > platform.salary_cap:
        violations.append(
            f"Total salary ${total_salary:,.0f} exceeds cap "
            f"${platform.salary_cap:,}"
        )

    min_spend = int(preset.get("min_salary_spend", 0))
    if min_spend > 0 and total_salary < min_spend:
        violations.append(
            f"Total salary ${total_salary:,.0f} is below the preset minimum "
            f"spend of ${min_spend:,}"
        )

    # Team presence
    if platform.requires_one_per_team:
        teams_in_lineup = {getattr(p, "team", None) for p in lineup_players}
        opps = {getattr(p, "opp", None) for p in lineup_players}
        # In a two-team showdown, each player's opp should also appear
        # as someone's team. If not, the lineup is missing a team.
        if opps and not (opps <= teams_in_lineup | {None}):
            missing_team = opps - teams_in_lineup
            if missing_team:
                violations.append(
                    "Lineup violates one-per-team rule; missing team(s): "
                    + ", ".join(str(t) for t in missing_team if t)
                )

    # Captain multiplier audit (scoring side)
    base_med = getattr(captain, "_base_median_score", None)
    cur_med = getattr(captain, "median_score", None)
    mult = float(platform.multiplier_points_factor)
    if base_med is not None and cur_med is not None:
        expected = float(base_med) * mult
        if not np.isclose(float(cur_med), expected, rtol=1e-6, atol=1e-6):
            violations.append(
                "Captain multiplier audit FAILED: displayed median "
                f"{cur_med:.4f} != base {base_med:.4f} * "
                f"{mult} = {expected:.4f}"
            )

    # If the caller supplied expected_captain_salary / expected_captain_projection
    # we can check them against the multiplied values directly.
    exp_cap_sal = getattr(captain, "_expected_captain_salary", None)
    if exp_cap_sal is not None and not np.isclose(captain_salary, float(exp_cap_sal)):
        violations.append(
            f"Captain salary audit FAILED: computed "
            f"${captain_salary:,.0f} != expected ${float(exp_cap_sal):,.0f}"
        )

    return violations


# ---------------------------------------------------------------------------
# Infeasibility diagnostics
# ---------------------------------------------------------------------------

def diagnose_infeasibility(
    locked: Sequence[Any],
    excluded: Sequence[Any],
    platform: PlatformConfig,
    preset: Mapping[str, Any],
    df: pd.DataFrame,
) -> list[str]:
    """Explain why no valid lineup exists given the current constraints.

    Returns a list of likely causes plus suggested relaxations. The
    diagnostics are produced in a fixed order so the UI can render them
    deterministically.
    """
    reasons: list[str] = []

    roster_size = platform.roster_size
    salary_cap = platform.salary_cap
    cap_mult = float(platform.multiplier_salary_factor)

    n_total = len(df)
    if n_total < roster_size:
        reasons.append(
            f"Slate has only {n_total} players, which is fewer than the "
            f"platform roster size ({roster_size}). No lineup is possible."
        )

    # Filter to the effective eligible pool
    excluded_ids = {getattr(p, "player_id", p) for p in excluded}
    locked_ids = {getattr(p, "player_id", p) for p in locked}

    if "player_id" in df.columns:
        eligible = df[~df["player_id"].isin(excluded_ids)]
    else:
        eligible = df

    if len(eligible) < roster_size:
        reasons.append(
            f"Only {len(eligible)} players remain after exclusions "
            f"({len(excluded_ids)} excluded); need at least {roster_size}."
        )

    if len(locked_ids) > roster_size:
        reasons.append(
            f"{len(locked_ids)} players locked but roster size is only "
            f"{roster_size}. Remove at least "
            f"{len(locked_ids) - roster_size} lock(s)."
        )

    # One-per-team feasibility
    if platform.requires_one_per_team and "team" in eligible.columns:
        teams_remaining = eligible["team"].unique()
        if len(teams_remaining) < 2:
            reasons.append(
                "Only one team remains in the eligible pool. Showdown "
                "requires at least one player from each team."
            )

    # Salary feasibility: can we even fit the minimum-priced roster?
    if "salary" in eligible.columns and len(eligible) >= roster_size:
        salaries = pd.to_numeric(eligible["salary"], errors="coerce").dropna()
        if len(salaries) >= roster_size:
            cheapest_sorted = salaries.sort_values().tolist()
            cheapest_util_bill = sum(cheapest_sorted[: roster_size - 1])
            cheapest_cap_bill = cheapest_sorted[0] * cap_mult
            min_lineup_cost = cheapest_util_bill + cheapest_cap_bill
            if min_lineup_cost > salary_cap:
                reasons.append(
                    f"Even the minimum-priced roster costs "
                    f"${min_lineup_cost:,.0f}, over the cap "
                    f"${salary_cap:,}. Relax exclusions or review salaries."
                )

    # Locked-players forced-cost feasibility
    if len(locked_ids) >= 1 and "player_id" in df.columns and "salary" in df.columns:
        locked_rows = df[df["player_id"].isin(locked_ids)]
        if len(locked_rows) == len(locked_ids):
            forced_cost = pd.to_numeric(locked_rows["salary"], errors="coerce").sum()
            if forced_cost > salary_cap:
                reasons.append(
                    f"Locked players alone cost ${float(forced_cost):,.0f}, "
                    f"over the cap ${salary_cap:,}. Unlock at least one."
                )

    # Minimum spend vs remaining salary room
    min_spend = int(preset.get("min_salary_spend", 0))
    if min_spend > salary_cap:
        reasons.append(
            f"Preset minimum spend ${min_spend:,} exceeds the salary cap "
            f"${salary_cap:,}. Lower the preset's min_salary_spend."
        )

    # Stack minimum vs available hitters per team
    min_stack = int(preset.get("min_stack_size", 0))
    if min_stack > 0 and "team" in eligible.columns and "is_hitter" in eligible.columns:
        hitter_counts = eligible[eligible["is_hitter"]]["team"].value_counts()
        if hitter_counts.empty or hitter_counts.max() < min_stack:
            reasons.append(
                f"No team has at least {min_stack} eligible hitters; "
                "preset min_stack_size cannot be satisfied. "
                "Lower min_stack_size or remove exclusions."
            )

    if not reasons:
        reasons.append(
            "No single-cause diagnosis found. Try progressively relaxing: "
            "(1) excluded players, (2) min_salary_spend, (3) min_stack_size, "
            "(4) allow_double_pitcher, then re-run."
        )

    return reasons
