"""Feature engineering for MLB Showdown scoring.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, sections
"Normalization Strategy" (including the two-team showdown note), "Scoring
Architecture" (A: hitter features, B: pitcher features), and "Calibration
Layer" (team-total damping).

This module provides:
- `rank_percentile`, `robust_zscore`, `standard_zscore`
- `normalize_feature` that dispatches by slate size
- `get_team_total_coefficient` — damped team-total weight per blueprint
- `compute_player_features` — adds per-player adjustment columns
- canonical helpers used across scoring, conflicts, and coherence:
  `player_salary_percentile`, `player_ceiling_percentile`,
  `most_common_team`, `get_stack_teams`
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

import numpy as np
import pandas as pd

from utils.constants import ORDER_ADJ_DEFAULT, ORDER_ADJ_MAP


# ---------------------------------------------------------------------------
# Normalization primitives
# ---------------------------------------------------------------------------

def rank_percentile(values: Iterable[float]) -> np.ndarray:
    """Rank values to a centered [-1, 1] percentile score.

    Uses `average` rank so ties are stable. A single-valued input returns 0.
    The output is centered so the feature is a zero-mean modifier rather
    than a strictly positive quantity. This matches the spirit of other
    normalizers returned from this module (standard/robust z-scores).
    """
    arr = pd.Series(np.asarray(values, dtype=float))
    n = arr.notna().sum()
    if n <= 1:
        return np.zeros(len(arr), dtype=float)
    # rank 1..n, divide to 0..1, then center to [-1, 1]
    ranks = arr.rank(method="average")
    pct = (ranks - 1.0) / max(n - 1, 1)
    return np.asarray((pct - 0.5) * 2.0, dtype=float)


def robust_zscore(values: Iterable[float]) -> np.ndarray:
    """Robust z-score using median and MAD (1.4826 scaling for normality)."""
    arr = np.asarray(list(values), dtype=float)
    if len(arr) == 0:
        return arr
    med = np.nanmedian(arr)
    mad = np.nanmedian(np.abs(arr - med))
    if not np.isfinite(mad) or mad == 0:
        return np.zeros_like(arr)
    return (arr - med) / (1.4826 * mad)


def standard_zscore(values: Iterable[float]) -> np.ndarray:
    """Population-style standard z-score."""
    arr = np.asarray(list(values), dtype=float)
    if len(arr) == 0:
        return arr
    mean = np.nanmean(arr)
    std = np.nanstd(arr)
    if not np.isfinite(std) or std == 0:
        return np.zeros_like(arr)
    return (arr - mean) / std


def normalize_feature(values: Iterable[float], slate_size: int) -> np.ndarray:
    """Dispatch the correct normalizer based on slate size (blueprint rule).

    - slate_size < 30     -> rank_percentile
    - 30 <= slate_size <= 60 -> robust_zscore
    - slate_size > 60     -> standard_zscore
    """
    if slate_size < 30:
        return rank_percentile(values)
    if slate_size <= 60:
        return robust_zscore(values)
    return standard_zscore(values)


# ---------------------------------------------------------------------------
# Calibration: damped team-total coefficient
# ---------------------------------------------------------------------------

def get_team_total_coefficient(team_a_implied: float, team_b_implied: float) -> float:
    """Damped team-total weighting for two-team showdown slates.

    Blueprint table:
        gap < 0.5 -> 0.03
        gap < 1.0 -> 0.045
        else      -> 0.06
    """
    gap = abs(float(team_a_implied) - float(team_b_implied))
    if gap < 0.5:
        return 0.03
    if gap < 1.0:
        return 0.045
    return 0.06


# ---------------------------------------------------------------------------
# Per-player feature adjustments (sections A and B of the blueprint)
# ---------------------------------------------------------------------------

def compute_player_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add feature-adjustment columns used by scoring.

    Hitters get:
        order_adj, team_total_adj, game_total_adj, value_adj, platoon_adj
    Pitchers get:
        favorite_adj, opp_team_total_adj, pitcher game_total_adj
    The baseline weight applied to ``team_total_adj`` for hitters is the
    damped coefficient from ``get_team_total_coefficient``.
    """
    out = df.copy().reset_index(drop=True)
    slate_size = len(out)

    # --- Slate-level normalizations (used by several columns) ---------------
    norm_team_total = normalize_feature(out["implied_team_score"].astype(float), slate_size)
    norm_game_total = normalize_feature(out["over_under"].astype(float), slate_size)
    norm_value = normalize_feature(out["value_projection"].astype(float), slate_size)

    # Pitcher-flavored normalizations: invert sign so lower opp team total
    # or lower game total scores higher.
    neg_opp_total = -out["opp"].map(
        out.groupby("team")["implied_team_score"].first().to_dict()
    ).astype(float)
    norm_neg_opp_total = normalize_feature(neg_opp_total, slate_size)
    norm_neg_game_total = normalize_feature(-out["over_under"].astype(float), slate_size)

    # --- Damped team-total coefficient (two-team showdown calibration) ------
    team_totals = out.groupby("team")["implied_team_score"].first()
    if len(team_totals) >= 2:
        top_two = team_totals.sort_values(ascending=False).head(2).tolist()
        team_total_coef = get_team_total_coefficient(top_two[0], top_two[1])
    else:
        team_total_coef = 0.06
    out["team_total_coef"] = team_total_coef

    # --- Hitter adjustments (section A) -------------------------------------
    order_adj = out["batting_order"].apply(
        lambda bo: ORDER_ADJ_MAP.get(int(bo), ORDER_ADJ_DEFAULT)
    )
    hitter_mask = out["is_hitter"].astype(bool)
    pitcher_mask = out["is_pitcher"].astype(bool)

    team_total_adj = team_total_coef * norm_team_total
    game_total_adj_hitter = 0.03 * norm_game_total
    value_adj = 0.03 * norm_value
    platoon_adj = np.where(out["platoon_edge"].eq("favorable"), 0.06, 0.0)

    out["order_adj"] = np.where(hitter_mask, order_adj, 0.0)
    out["team_total_adj"] = np.where(hitter_mask, team_total_adj, 0.0)
    out["game_total_adj"] = np.where(hitter_mask, game_total_adj_hitter, 0.0)
    out["value_adj"] = np.where(hitter_mask, value_adj, 0.0)
    out["platoon_adj"] = np.where(hitter_mask, platoon_adj, 0.0)

    # --- Pitcher adjustments (section B) ------------------------------------
    favorite_adj = np.where(out["is_favorite"], 0.06, -0.03)
    opp_team_total_adj = 0.10 * norm_neg_opp_total
    game_total_adj_pitcher = 0.05 * norm_neg_game_total

    out["favorite_adj"] = np.where(pitcher_mask, favorite_adj, 0.0)
    out["opp_team_total_adj"] = np.where(pitcher_mask, opp_team_total_adj, 0.0)
    # Overwrite game_total_adj for pitcher rows with the pitcher-specific term.
    out["game_total_adj"] = np.where(
        pitcher_mask, game_total_adj_pitcher, out["game_total_adj"]
    )

    return out


# ---------------------------------------------------------------------------
# Canonical helper functions used across the codebase
# ---------------------------------------------------------------------------

def _salary(player: Any) -> float:
    return float(getattr(player, "salary", None) if not isinstance(player, dict) else player["salary"])


def _ceiling(player: Any) -> float:
    val = getattr(player, "ceiling_score", None)
    if val is None and isinstance(player, dict):
        val = player.get("ceiling_score")
    return float(val)


def _team(player: Any) -> str:
    val = getattr(player, "team", None)
    if val is None and isinstance(player, dict):
        val = player.get("team")
    return str(val)


def player_salary_percentile(player: Any, df: pd.DataFrame) -> float:
    """Fraction of rows in ``df`` with salary <= this player's salary.

    Matches the blueprint's helper signature; callers are responsible for
    filtering ``df`` to the appropriate pool (e.g. captain-eligible slate)
    before calling when percentile semantics require it.
    """
    sal = _salary(player)
    salaries = pd.to_numeric(df["salary"], errors="coerce")
    return float((salaries <= sal).mean())


def player_ceiling_percentile(player: Any, df: pd.DataFrame) -> float:
    """Fraction of rows in ``df`` with ceiling_score <= this player's ceiling."""
    ceiling = _ceiling(player)
    ceilings = pd.to_numeric(df["ceiling_score"], errors="coerce")
    return float((ceilings <= ceiling).mean())


def most_common_team(players: Iterable[Any]) -> str:
    """Return the most common team among an iterable of player-like objects."""
    teams = [_team(p) for p in players]
    if not teams:
        raise ValueError("most_common_team() received an empty iterable")
    return Counter(teams).most_common(1)[0][0]


def get_stack_teams(lineup: Any) -> tuple[str, str | None]:
    """Return ``(primary_stack_team, secondary_stack_team)`` for a lineup.

    Rules (blueprint):
    - primary stack = the team with the most hitters
    - secondary stack is assigned **only** when that team has >= 2 hitters;
      a single bring-back hitter does not count as a secondary stack
    """
    hitters = [
        p for p in lineup.all_players
        if bool(getattr(p, "is_hitter", False))
    ]
    if not hitters:
        raise ValueError("get_stack_teams() requires a lineup with at least one hitter")

    counts = Counter(_team(p) for p in hitters)
    ranked = counts.most_common()
    primary = ranked[0][0]
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] >= 2 else None
    return primary, secondary
