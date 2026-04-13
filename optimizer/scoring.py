"""Scoring layer.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Scoring Architecture" subsections A (hitter median), B (pitcher median),
C (ceiling + ceiling boost rule), D (captain score including
hitter_captain_preference), and E (captain viability floor).

Tracks multiple scores per player rather than collapsing early:
    median_score, ceiling_score, ceiling_boost, captain_score
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from optimizer.features import compute_player_features, normalize_feature
from optimizer.platform_rules import PlatformConfig


# ---------------------------------------------------------------------------
# A. Hitter median
# ---------------------------------------------------------------------------

def score_hitter_median(
    row: Mapping[str, Any], features: Mapping[str, Any], slate_size: int
) -> float:
    """Blueprint section A.

    hitter_median_score = ppg_projection * (
        1 + form_signal + order_adj + team_total_adj
          + game_total_adj + value_adj + platoon_adj
    )
    """
    base = float(row["ppg_projection"])
    total = (
        1.0
        + float(row.get("form_signal", 0.0))
        + float(features.get("order_adj", 0.0))
        + float(features.get("team_total_adj", 0.0))
        + float(features.get("game_total_adj", 0.0))
        + float(features.get("value_adj", 0.0))
        + float(features.get("platoon_adj", 0.0))
    )
    return base * total


# ---------------------------------------------------------------------------
# B. Pitcher median
# ---------------------------------------------------------------------------

def score_pitcher_median(
    row: Mapping[str, Any], features: Mapping[str, Any], slate_size: int
) -> float:
    """Blueprint section B.

    pitcher_median_score = ppg_projection * (
        1 + form_signal + favorite_adj + opp_team_total_adj + game_total_adj
    )
    """
    base = float(row["ppg_projection"])
    total = (
        1.0
        + float(row.get("form_signal", 0.0))
        + float(features.get("favorite_adj", 0.0))
        + float(features.get("opp_team_total_adj", 0.0))
        + float(features.get("game_total_adj", 0.0))
    )
    return base * total


# ---------------------------------------------------------------------------
# C. Ceiling (hitter + pitcher) and ceiling_boost
# ---------------------------------------------------------------------------

HITTER_CEILING_BASE = 1.18
HITTER_CEILING_CAP = 1.40
PITCHER_CEILING_BASE = 1.15
PITCHER_CEILING_CAP = 1.35


def score_hitter_ceiling(median_score: float, features: Mapping[str, Any]) -> float:
    """Blueprint section C (hitter).

    Only positive adjustments contribute. Multiplier is capped at 1.40.
    """
    upside_order = max(float(features.get("order_adj", 0.0)), 0.0)
    upside_team_total = max(float(features.get("team_total_adj", 0.0)), 0.0)
    upside_platoon = max(float(features.get("platoon_adj", 0.0)), 0.0)
    upside_form = max(float(features.get("form_signal", 0.0)), 0.0)

    mult = min(
        HITTER_CEILING_BASE
        + upside_order
        + upside_team_total
        + upside_platoon
        + upside_form,
        HITTER_CEILING_CAP,
    )
    return float(median_score) * mult


def score_pitcher_ceiling(median_score: float, features: Mapping[str, Any]) -> float:
    """Blueprint section C (pitcher). Multiplier capped at 1.35."""
    mult = min(
        PITCHER_CEILING_BASE
        + max(float(features.get("favorite_adj", 0.0)), 0.0)
        + max(float(features.get("opp_team_total_adj", 0.0)), 0.0)
        + max(float(features.get("form_signal", 0.0)), 0.0),
        PITCHER_CEILING_CAP,
    )
    return float(median_score) * mult


def compute_ceiling_boost(ceiling_score: float, median_score: float) -> float:
    """Incremental upside layer above median.

    Blueprint rule: ceiling_boost = ceiling_score - median_score. Never set
    ceiling_boost = ceiling_score, or median projection will be double-
    counted in the raw Stage 1 solver objective.
    """
    return float(ceiling_score) - float(median_score)


# ---------------------------------------------------------------------------
# D. Captain score (section D + hitter_captain_preference mechanic)
# ---------------------------------------------------------------------------

def score_captain(
    ceiling_score: float,
    platform: PlatformConfig,
    ownership: float | None = None,
    leverage_weight: float = 0.5,
    hitter_captain_preference: float = 1.0,
    is_hitter: bool = True,
) -> float:
    """Blueprint section D.

    captain_score = ceiling_score * multiplier_points_factor
    If ownership is present (and non-None / > 0):
        captain_score *= (1 / ownership_projection) ** leverage_weight
    Then apply the hitter_captain_preference bias:
        hitter captain:  *= hitter_captain_preference
        pitcher captain: *= (2.0 - hitter_captain_preference)
    """
    score = float(ceiling_score) * float(platform.multiplier_points_factor)

    if ownership is not None and not (isinstance(ownership, float) and np.isnan(ownership)):
        try:
            own = float(ownership)
        except (TypeError, ValueError):
            own = 0.0
        if own > 0:
            score *= (1.0 / own) ** float(leverage_weight)

    if is_hitter:
        score *= float(hitter_captain_preference)
    else:
        score *= 2.0 - float(hitter_captain_preference)

    return score


# ---------------------------------------------------------------------------
# Aggregate scorer
# ---------------------------------------------------------------------------

def compute_all_scores(
    df: pd.DataFrame, platform: PlatformConfig, preset: Mapping[str, Any]
) -> pd.DataFrame:
    """Score every player and return the enriched DataFrame.

    Adds: median_score, ceiling_score, ceiling_boost, captain_score. Runs
    ``compute_player_features`` first so the caller can pass either a raw
    prepared frame or a frame that already has feature columns; duplicate
    feature computation is idempotent because features are derived from the
    original slate columns.
    """
    feats = compute_player_features(df)
    slate_size = len(feats)

    leverage_weight = float(preset.get("leverage_weight", 0.5))
    hitter_captain_preference = float(preset.get("hitter_captain_preference", 1.0))

    has_ownership = "ownership_projection" in feats.columns

    median_scores = np.zeros(len(feats), dtype=float)
    ceiling_scores = np.zeros(len(feats), dtype=float)
    captain_scores = np.zeros(len(feats), dtype=float)

    for i, row in feats.iterrows():
        is_pitcher = bool(row["is_pitcher"])
        if is_pitcher:
            med = score_pitcher_median(row, row, slate_size)
            ceil = score_pitcher_ceiling(med, row)
        else:
            med = score_hitter_median(row, row, slate_size)
            ceil = score_hitter_ceiling(med, row)

        own = None
        if has_ownership:
            raw_own = row.get("ownership_projection")
            if raw_own is not None and not (isinstance(raw_own, float) and np.isnan(raw_own)):
                own = raw_own

        cap = score_captain(
            ceiling_score=ceil,
            platform=platform,
            ownership=own,
            leverage_weight=leverage_weight,
            hitter_captain_preference=hitter_captain_preference,
            is_hitter=not is_pitcher,
        )

        median_scores[i] = med
        ceiling_scores[i] = ceil
        captain_scores[i] = cap

    feats["median_score"] = median_scores
    feats["ceiling_score"] = ceiling_scores
    feats["ceiling_boost"] = feats["ceiling_score"] - feats["median_score"]
    feats["captain_score"] = captain_scores

    return feats


# ---------------------------------------------------------------------------
# E. Captain viability pool
# ---------------------------------------------------------------------------

def get_captain_pool(df: pd.DataFrame, preset: Mapping[str, Any]) -> pd.DataFrame:
    """Filter ``df`` to viable captain candidates per section E.

    A candidate must pass BOTH:
      1. raw ceiling >= ``captain_viability_pctile`` percentile of the slate
      2. raw-ceiling ratio to the top slate ceiling >= ``captain_relative_floor``
    """
    if df.empty or "ceiling_score" not in df.columns:
        return df.iloc[0:0]

    pctile_threshold = float(preset.get("captain_viability_pctile", 0)) / 100.0
    relative_floor = float(preset.get("captain_relative_floor", 0.0))

    ceilings = df["ceiling_score"].astype(float)
    # Viability percentile threshold on raw ceiling score.
    threshold_value = float(np.quantile(ceilings, pctile_threshold)) if len(ceilings) else 0.0

    top_ceiling = float(ceilings.max()) if len(ceilings) else 0.0
    if top_ceiling <= 0:
        return df.iloc[0:0]

    relative = ceilings / top_ceiling
    passes = (ceilings >= threshold_value) & (relative >= relative_floor)
    return df[passes].copy()
