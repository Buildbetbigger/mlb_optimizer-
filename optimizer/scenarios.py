"""Scenario Engine.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Scenario Engine" (SCRIPT_MODIFIERS, SCRIPT_PREFERRED_ARCHETYPES,
default_script_weights, get_effective_script_weights, modifier unit
rules, qualifies_for_wraparound_bonus).

The app produces lineups across plausible game scripts. Each script
modifies player-level ceilings via direct multipliers and contributes
lineup-level bonuses (stack, double-pitcher, bottom-order) that are
added once per qualifying structure to avoid double-counting.

Modifier unit rules (blueprint):
- ``*_mult`` values are direct multipliers on player-level scores
- ``*_bonus`` values are applied as a percentage of a qualifying stack
  or structure's average median score, converted into point-scale, and
  added once per qualifying structure
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from utils.constants import SCRIPT_MODIFIERS


__all__ = [
    "default_script_weights",
    "get_effective_script_weights",
    "apply_script_modifiers",
    "qualifies_for_wraparound_bonus",
    "compute_script_lineup_bonus",
]


# ---------------------------------------------------------------------------
# Probabilistic script weighting
# ---------------------------------------------------------------------------

def default_script_weights(over_under: float, abs_spread: float) -> dict:
    """Return baseline script weights derived from betting context.

    Exact function from the blueprint. The ``abs_spread`` shift moves
    weight from ``underdog`` toward ``fav_controls`` when the spread is
    >= 1.5, capped at 0.08. Final weights are renormalized to sum to 1.0.
    """
    if over_under <= 7.5:
        weights = {
            "pitcher_duel": 0.30,
            "fav_controls": 0.25,
            "slugfest": 0.10,
            "underdog": 0.20,
            "wraparound": 0.15,
        }
    elif over_under >= 9.5:
        weights = {
            "pitcher_duel": 0.05,
            "fav_controls": 0.20,
            "slugfest": 0.40,
            "underdog": 0.20,
            "wraparound": 0.15,
        }
    else:
        weights = {
            "pitcher_duel": 0.15,
            "fav_controls": 0.25,
            "slugfest": 0.25,
            "underdog": 0.20,
            "wraparound": 0.15,
        }

    if abs_spread >= 1.5:
        shift = min(0.08, 0.02 * abs_spread)
        weights["fav_controls"] += shift
        weights["underdog"] -= shift

    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def get_effective_script_weights(
    slate_ou: float, slate_spread: float, preset: Mapping[str, Any]
) -> dict:
    """Use the preset's full override when provided, else fall back to
    the slate-derived defaults.

    Per the blueprint: ``script_weights_override`` fully replaces the
    base script weights; it is not a partial patch. Validation that
    the override sums to 1.0 (or renormalization) is the caller's
    responsibility - we still renormalize defensively here so callers
    can't accidentally inject a non-normalized distribution.
    """
    override = preset.get("script_weights_override") if preset else None
    if override:
        total = sum(float(v) for v in override.values())
        if total <= 0:
            return default_script_weights(float(slate_ou), abs(float(slate_spread)))
        return {k: float(v) / total for k, v in override.items()}
    return default_script_weights(float(slate_ou), abs(float(slate_spread)))


# ---------------------------------------------------------------------------
# Wraparound qualification helper (exact blueprint function)
# ---------------------------------------------------------------------------

def qualifies_for_wraparound_bonus(
    lineup: Any, team: str, slate_median_team_total: float
) -> bool:
    hitters = [
        p for p in lineup.all_players
        if p.is_hitter and p.team == team and 6 <= p.batting_order <= 9
    ]
    orders = sorted({p.batting_order for p in hitters})
    team_total = max(
        (p.implied_team_score for p in lineup.all_players if p.team == team),
        default=0.0,
    )

    has_two_from_79 = sum(1 for o in orders if 7 <= o <= 9) >= 2
    has_correlated_value_chain = orders[:2] == [6, 7] or orders[:3] == [6, 7, 8]

    return team_total > slate_median_team_total and (
        has_two_from_79 or has_correlated_value_chain
    )


# ---------------------------------------------------------------------------
# Apply script modifiers to a DataFrame (player-level *_mult work)
# ---------------------------------------------------------------------------

def apply_script_modifiers(
    df: pd.DataFrame,
    script_name: str,
    slate_data: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Return a copy of ``df`` with the script's player-level multipliers
    applied to ``ceiling_score`` (and a corresponding ``captain_score``
    refresh when the column is present).

    Adds two diagnostic columns so the lineup-assembly layer can apply
    the once-per-structure bonuses without recomputing them:
        ``_script_team_stack_bonus`` -- per-team point-scale value for
            ``fav_stack_bonus`` / ``dog_stack_bonus`` (whichever applies)
        ``_script_team_bottom_bonus`` -- per-team value for the
            ``wraparound`` script's ``bottom_order_bonus``; 0 elsewhere
    Also annotates ``df.attrs``:
        ``script_name``, ``script_modifier``,
        ``script_double_pitcher_bonus`` -- point-scale value (one per
        lineup if double-pitcher is selected)
    """
    if script_name not in SCRIPT_MODIFIERS:
        raise ValueError(
            f"Unknown script '{script_name}'. Valid scripts: "
            + ", ".join(sorted(SCRIPT_MODIFIERS))
        )

    out = df.copy().reset_index(drop=True)
    mod = SCRIPT_MODIFIERS[script_name]

    hitter_mult = float(mod.get("hitter_ceiling_mult", 1.0))
    pitcher_mult = float(mod.get("pitcher_ceiling_mult", 1.0))
    fav_pitcher_mult = float(mod.get("fav_pitcher_mult", 1.0))
    dog_pitcher_mult = float(mod.get("dog_pitcher_mult", 1.0))

    # --- *_mult: direct player-level multipliers -----------------------
    is_pitcher = out["is_pitcher"].astype(bool).to_numpy()
    is_hitter = out["is_hitter"].astype(bool).to_numpy()
    is_fav = out["is_favorite"].astype(bool).to_numpy() if "is_favorite" in out.columns else np.zeros(len(out), bool)
    is_dog = out["is_underdog"].astype(bool).to_numpy() if "is_underdog" in out.columns else np.zeros(len(out), bool)

    mult_factor = np.ones(len(out), dtype=float)
    mult_factor[is_hitter] *= hitter_mult
    mult_factor[is_pitcher] *= pitcher_mult
    mult_factor[is_pitcher & is_fav] *= fav_pitcher_mult
    mult_factor[is_pitcher & is_dog] *= dog_pitcher_mult

    if "ceiling_score" in out.columns:
        out["ceiling_score"] = out["ceiling_score"].astype(float) * mult_factor
    # Refresh ceiling_boost so downstream Stage 1 / Stage 2 stay coherent.
    if "median_score" in out.columns and "ceiling_score" in out.columns:
        out["ceiling_boost"] = out["ceiling_score"] - out["median_score"]

    # --- *_bonus: stack-level (avg_stack_median * weight) ---------------
    fav_stack_bonus = float(mod.get("fav_stack_bonus", 0.0))
    dog_stack_bonus = float(mod.get("dog_stack_bonus", 0.0))

    team_stack_bonus = pd.Series(0.0, index=out.index, dtype=float)
    if "median_score" in out.columns and "team" in out.columns:
        team_groups = out[out["is_hitter"]].groupby("team")
        for team, group in team_groups:
            avg_med = float(group["median_score"].mean())
            row = group.iloc[0]
            if bool(row.get("is_favorite", False)):
                weight = fav_stack_bonus
            elif bool(row.get("is_underdog", False)):
                weight = dog_stack_bonus
            else:
                weight = 0.0
            value = avg_med * weight
            team_stack_bonus.loc[out["team"] == team] = value
    out["_script_team_stack_bonus"] = team_stack_bonus

    # --- bottom_order_bonus (wraparound script only) -------------------
    bottom_bonus_weight = float(mod.get("bottom_order_bonus", 0.0))
    team_bottom_bonus = pd.Series(0.0, index=out.index, dtype=float)
    if bottom_bonus_weight and "median_score" in out.columns:
        # Per-team value: avg median of hitters in batting order 6-9.
        for team, group in out[out["is_hitter"]].groupby("team"):
            bottom_hitters = group[group["batting_order"].between(6, 9)]
            if len(bottom_hitters):
                avg_med = float(bottom_hitters["median_score"].mean())
                team_bottom_bonus.loc[out["team"] == team] = avg_med * bottom_bonus_weight
    out["_script_team_bottom_bonus"] = team_bottom_bonus

    # --- double_pitcher_bonus (lineup-level scalar) --------------------
    double_p_weight = float(mod.get("double_pitcher_bonus", 0.0))
    pitcher_meds = out[out["is_pitcher"]]["median_score"].astype(float)
    avg_pitcher_med = float(pitcher_meds.mean()) if len(pitcher_meds) else 0.0
    double_pitcher_bonus = avg_pitcher_med * double_p_weight

    out.attrs["script_name"] = script_name
    out.attrs["script_modifier"] = dict(mod)
    out.attrs["script_double_pitcher_bonus"] = double_pitcher_bonus

    return out


# ---------------------------------------------------------------------------
# Lineup-assembly helper: sum once-per-structure bonuses
# ---------------------------------------------------------------------------

def compute_script_lineup_bonus(
    lineup_players: list[Any],
    df_with_modifiers: pd.DataFrame,
    script_name: str,
    slate_median_team_total: float | None = None,
) -> float:
    """Return the once-per-structure script bonus contribution for a lineup.

    Adds (at most once per qualifying structure):
    - team_stack_bonus for the lineup's primary stack team
    - team_stack_bonus for the secondary stack team (>=2 hitters)
    - double_pitcher_bonus when two pitchers are selected
    - team_bottom_bonus for any team that satisfies
      qualifies_for_wraparound_bonus under the wraparound script
    """
    from collections import Counter

    if not lineup_players:
        return 0.0

    hitters = [p for p in lineup_players if getattr(p, "is_hitter", False)]
    counts = Counter(p.team for p in hitters)
    ranked = counts.most_common()
    primary = ranked[0][0] if ranked else None
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] >= 2 else None

    bonus = 0.0
    if "_script_team_stack_bonus" in df_with_modifiers.columns:
        team_to_value = (
            df_with_modifiers.groupby("team")["_script_team_stack_bonus"]
            .first()
            .to_dict()
        )
        if primary in team_to_value:
            bonus += float(team_to_value[primary])
        if secondary in team_to_value:
            bonus += float(team_to_value[secondary])

    pitchers = [p for p in lineup_players if getattr(p, "is_pitcher", False)]
    if len(pitchers) >= 2:
        bonus += float(df_with_modifiers.attrs.get("script_double_pitcher_bonus", 0.0))

    if (
        script_name == "wraparound"
        and "_script_team_bottom_bonus" in df_with_modifiers.columns
        and slate_median_team_total is not None
    ):
        team_to_bottom = (
            df_with_modifiers.groupby("team")["_script_team_bottom_bonus"]
            .first()
            .to_dict()
        )
        # Build a minimal lineup-view for the qualification helper.
        view = type("L", (), {"all_players": lineup_players})()
        for team in team_to_bottom:
            if qualifies_for_wraparound_bonus(view, team, slate_median_team_total):
                bonus += float(team_to_bottom[team])

    return bonus
