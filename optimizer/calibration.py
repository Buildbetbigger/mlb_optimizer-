"""Calibration Layer.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Calibration Layer".

This module sits between core scoring and the diagnostics/review layer.
Calibration is where the app learns how aggressively to behave on a given
slate without rewriting the underlying player-scoring model.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

# The damping function lives in features.py (where it's called during
# per-player feature computation). Re-export it here so the calibration
# layer also exposes the canonical symbol.
from optimizer.features import get_team_total_coefficient as _get_team_total_coefficient
from optimizer.scoring import get_captain_pool


__all__ = [
    "TARGET_VIABLE_CAPTAINS",
    "compute_slate_concentration",
    "get_team_total_coefficient",
    "check_salary_band",
    "calibrate_review_pool_targets",
]


TARGET_VIABLE_CAPTAINS = 8


def get_team_total_coefficient(team_a_implied: float, team_b_implied: float) -> float:
    """Re-export of the damped team-total coefficient function.

    Kept in features.py to avoid a circular import chain when scoring
    consumes it, but re-exported here so ``optimizer.calibration`` is the
    natural home of the calibration-facing symbols.
    """
    return _get_team_total_coefficient(team_a_implied, team_b_implied)


# ---------------------------------------------------------------------------
# Slate concentration detector
# ---------------------------------------------------------------------------

def compute_slate_concentration(
    df: pd.DataFrame, preset: Mapping[str, Any]
) -> dict:
    """Return the slate-concentration score and its label.

    Components:
        captain_gap   (0.40 weight) -- (ceiling[0] - ceiling[2]) / ceiling[0]
        viable_score  (0.30 weight) -- 1 - viable_captain_count / TARGET
                                       (clipped at 0)
        total_gap     (0.30 weight) -- |max - min| / mean team-total

    Labels:
        >= 0.55 -> "high"
        >= 0.30 -> "medium"
        else   -> "low"
    """
    captain_pool = get_captain_pool(df, preset)
    ceilings = sorted(captain_pool["ceiling_score"].values, reverse=True) if not captain_pool.empty else []

    if len(ceilings) >= 3:
        captain_gap = (ceilings[0] - ceilings[2]) / max(ceilings[0], 1e-9)
    else:
        captain_gap = 1.0

    team_totals = df.groupby("team")["implied_team_score"].first()
    total_gap = (
        abs(team_totals.max() - team_totals.min()) / max(team_totals.mean(), 1e-9)
        if len(team_totals) >= 2
        else 0.0
    )

    viable_count = len(captain_pool)
    viable_score = max(0.0, 1.0 - (viable_count / TARGET_VIABLE_CAPTAINS))

    concentration = 0.40 * captain_gap + 0.30 * viable_score + 0.30 * total_gap

    if concentration >= 0.55:
        label = "high"
    elif concentration >= 0.30:
        label = "medium"
    else:
        label = "low"

    return {
        "score": round(float(concentration), 3),
        "label": label,
        "captain_gap": round(float(captain_gap), 3),
        "viable_score": round(float(viable_score), 3),
        "total_gap": round(float(total_gap), 3),
        "viable_captain_count": int(viable_count),
    }


# ---------------------------------------------------------------------------
# Salary-band warning
# ---------------------------------------------------------------------------

def check_salary_band(lineup: Any, preset: Mapping[str, Any]) -> str | None:
    """Return a warning string if the lineup spend is outside the preset band.

    The blueprint treats these as calibration ranges for warnings and
    diagnostics, not hard cap-legality rules.
    """
    total_salary = int(getattr(lineup, "total_salary", 0))
    band_min = preset.get("expected_salary_band_min")
    band_max = preset.get("expected_salary_band_max")

    if band_min is None or band_max is None:
        return None

    band_min = int(band_min)
    band_max = int(band_max)

    # `large_mme`-style presets set band_min=0 (informational monitoring
    # only). Treat that as "no warning".
    if band_min <= 0 and band_max >= 50000:
        return None

    if total_salary < band_min:
        return (
            f"Lineup spend ${total_salary:,} is below the preset "
            f"expected salary band (${band_min:,}-${band_max:,})"
        )
    if total_salary > band_max:
        return (
            f"Lineup spend ${total_salary:,} exceeds the preset "
            f"expected salary band (${band_min:,}-${band_max:,})"
        )
    return None


# ---------------------------------------------------------------------------
# Review-pool calibration
# ---------------------------------------------------------------------------

def calibrate_review_pool_targets(
    slate_concentration: Mapping[str, Any], preset: Mapping[str, Any]
) -> dict:
    """Adjust review-pool diversity targets based on slate concentration.

    Rules (blueprint):
    - high concentration: allow a narrower review pool, fewer distinct
      captains required
    - medium / low: require broader captain representation
    - never force review-pool diversity that would admit captains below
      the preset viability floor
    """
    label = slate_concentration.get("label", "medium")

    # Preset starting points (optional; fall back to sensible defaults
    # so this function is safe on presets that don't define every field).
    base_top_review_count = int(preset.get("top_review_count", 10))
    base_min_distinct = int(preset.get("review_min_distinct_captains", 2))
    base_max_same = int(preset.get("review_max_same_captain", 4))

    if label == "high":
        top_review_count = max(5, base_top_review_count - 2)
        min_distinct_captains = max(1, base_min_distinct - 1)
        max_same_captain = min(base_top_review_count, base_max_same + 2)
    elif label == "low":
        top_review_count = base_top_review_count + 2
        min_distinct_captains = base_min_distinct + 2
        max_same_captain = max(1, base_max_same - 1)
    else:  # medium
        top_review_count = base_top_review_count
        min_distinct_captains = base_min_distinct + 1
        max_same_captain = base_max_same

    return {
        "label": label,
        "top_review_count": top_review_count,
        "min_distinct_captains": min_distinct_captains,
        "max_same_captain": max_same_captain,
        "notes": _review_pool_note(label),
    }


def _review_pool_note(label: str) -> str:
    if label == "high":
        return (
            "Slate is narrow: one or two captains clearly separate. "
            "A tighter review pool is acceptable."
        )
    if label == "low":
        return (
            "Slate is open: several captains and stack scripts are tightly "
            "clustered. Widen review-pool diversity."
        )
    return (
        "Slate is moderately concentrated. Apply mild review-pool "
        "diversification around the top captain tier."
    )
