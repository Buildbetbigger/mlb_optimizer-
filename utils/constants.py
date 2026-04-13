"""Canonical constants for the MLB Showdown GPP Optimizer.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md.
Every tunable value here has a concrete default in the blueprint.
"""

# ---------------------------------------------------------------------------
# Platform salary caps (Platform constants section)
# ---------------------------------------------------------------------------
DK_SHOWDOWN_SALARY_CAP = 50000
FD_SINGLE_GAME_SALARY_CAP = 60000


# ---------------------------------------------------------------------------
# Form clip bounds (section 7: restrained form deltas)
# ---------------------------------------------------------------------------
L5_FORM_CLIP = 0.12
L10_FORM_CLIP = 0.08
FORM_SIGNAL_CLIP = 0.10


# ---------------------------------------------------------------------------
# Batting-order adjustment map (section A: Hitter median score)
# ---------------------------------------------------------------------------
ORDER_ADJ_MAP = {
    1: 0.08,
    2: 0.07,
    3: 0.09,
    4: 0.09,
    5: 0.06,
    6: 0.03,
    7: 0.00,
    8: -0.03,
    9: -0.04,
}
# default for unknown / missing batting order
ORDER_ADJ_DEFAULT = -0.05


# ---------------------------------------------------------------------------
# Correlation-engine bonus weights (Correlation Engine section)
# All values are percentage weights, not raw points. They must be converted
# into point-scale bonuses before being added to a lineup score.
# ---------------------------------------------------------------------------
SAME_TEAM_BONUS = 0.05
ADJACENT_ORDER_BONUS = 0.15
HEART_OF_ORDER_BONUS = 0.10
TOP_OF_ORDER_BONUS = 0.10
WRAPAROUND_BONUS = 0.05
FAVORABLE_PLATOON_BONUS = 0.06
PITCHER_CONFLICT_PENALTY = -0.12
OPPOSING_CAPTAIN_PENALTY = -0.20


# ---------------------------------------------------------------------------
# Stage 1 solver conflict penalties (point-scale deductions applied directly
# to the raw solver objective). These are distinct from the coherence-internal
# conflict weights further below.
# ---------------------------------------------------------------------------
SOLVER_CONFLICT_PENALTIES = {
    "soft": 0.50,
    "medium": 1.50,
    "hard": 4.00,
}


# ---------------------------------------------------------------------------
# Conflict code vocabulary (Conflict Taxonomy section)
# ---------------------------------------------------------------------------
CONFLICT_CODES = {
    "PITCHER_SINGLE_RUNBACK": "soft",
    "PITCHER_SINGLE_RUNBACK_MINOR": "soft",
    "OFFSTACK_PREMIUM_BAT": "soft",
    "PITCHER_TWO_OPP_HITTERS": "medium",
    "UNCORRELATED_PREMIUM": "medium",
    "DOUBLE_PITCHER_MODERATE": "medium",
    "CAPTAIN_VS_OWN_PITCHER": "hard",
    "PITCHER_OPP_MINISTACK": "hard",
    "BOTTOM_ORDER_CAPTAIN_NO_PATH": "hard",
    "DOUBLE_PITCHER_SLUGFEST": "hard",
}


# ---------------------------------------------------------------------------
# Stage 2 lineup-score weights (Scoring Architecture section).
# Used for normalized weighted final_optimizer_score only.
# ---------------------------------------------------------------------------
LINEUP_SCORE_WEIGHTS = {
    "w_projection": 0.30,
    "w_ceiling": 0.30,
    "w_correlation": 0.20,
    "w_uniqueness": 0.05,
    "w_coherence": 0.10,
    "w_conflict": 0.05,
}


# ---------------------------------------------------------------------------
# Portfolio-level overlap penalties (Portfolio Construction section).
# Used for greedy diminishing-returns portfolio selection.
# ---------------------------------------------------------------------------
DEFAULT_PORTFOLIO_OVERLAP_PENALTIES = {
    "captain_overlap_penalty": 3.00,
    "core_3man_overlap_penalty": 2.00,
    "core_4man_overlap_penalty": 3.50,
    "stack_overlap_penalty": 1.50,
    "script_overlap_penalty": 1.00,
    "salary_shape_overlap_penalty": 0.50,
}


# ---------------------------------------------------------------------------
# Script modifiers (Scenario Engine section).
# `*_mult` values are direct multipliers on player-level scores.
# `*_bonus` values are applied as a percentage of a qualifying stack's or
# structure's average median score, converted to point-scale, and added
# once per qualifying structure.
# ---------------------------------------------------------------------------
SCRIPT_MODIFIERS = {
    "pitcher_duel": {
        "hitter_ceiling_mult": 0.95,
        "pitcher_ceiling_mult": 1.10,
        "fav_pitcher_mult": 1.04,
        "dog_pitcher_mult": 1.04,
        "double_pitcher_bonus": 0.08,
        "fav_stack_bonus": 0.00,
        "dog_stack_bonus": 0.00,
    },
    "fav_controls": {
        "hitter_ceiling_mult": 1.00,
        "pitcher_ceiling_mult": 1.00,
        "fav_pitcher_mult": 1.06,
        "dog_pitcher_mult": 0.92,
        "double_pitcher_bonus": 0.00,
        "fav_stack_bonus": 0.05,
        "dog_stack_bonus": -0.03,
    },
    "slugfest": {
        "hitter_ceiling_mult": 1.08,
        "pitcher_ceiling_mult": 0.90,
        "fav_pitcher_mult": 0.95,
        "dog_pitcher_mult": 0.95,
        "double_pitcher_bonus": -0.15,
        "fav_stack_bonus": 0.03,
        "dog_stack_bonus": 0.03,
    },
    "underdog": {
        "hitter_ceiling_mult": 1.00,
        "pitcher_ceiling_mult": 1.00,
        "fav_pitcher_mult": 0.92,
        "dog_pitcher_mult": 1.06,
        "double_pitcher_bonus": 0.00,
        "fav_stack_bonus": -0.03,
        "dog_stack_bonus": 0.06,
    },
    "wraparound": {
        "hitter_ceiling_mult": 1.00,
        "pitcher_ceiling_mult": 1.00,
        "fav_pitcher_mult": 1.00,
        "dog_pitcher_mult": 1.00,
        "double_pitcher_bonus": 0.00,
        "fav_stack_bonus": 0.00,
        "dog_stack_bonus": 0.00,
        "bottom_order_bonus": 0.04,
    },
}


# ---------------------------------------------------------------------------
# Preferred captain archetypes per script (controlled vocabulary).
# ---------------------------------------------------------------------------
SCRIPT_PREFERRED_ARCHETYPES = {
    "pitcher_duel": ["ace suppression captain"],
    "fav_controls": ["premium slugger", "top-order volume bat"],
    "slugfest": ["premium slugger", "top-order volume bat"],
    "underdog": ["efficient mid-tier bat", "top-order volume bat"],
    "wraparound": ["wraparound salary-release captain", "efficient mid-tier bat"],
}


# ---------------------------------------------------------------------------
# Salary-shape buckets for portfolio overlap comparison.
# Each entry: (lo, hi, label) — inclusive bounds on salary_left.
# ---------------------------------------------------------------------------
SALARY_LEFT_BUCKETS = [
    (0, 500, "tight"),
    (501, 1500, "modest"),
    (1501, 3000, "loose"),
    (3001, 50000, "extreme"),
]


# ---------------------------------------------------------------------------
# Coherence constants (Coherence Engine section).
# Bounded penalties used inside compute_coherence().
# ---------------------------------------------------------------------------
PARTIAL_SCRIPT_FIT = 0.50
OFF_STACK_CAPTAIN_ALIGNMENT = 0.60
SOFT_CONFLICT_PENALTY = 0.05
MEDIUM_CONFLICT_PENALTY = 0.15
HARD_CONFLICT_PENALTY = 0.30


# ---------------------------------------------------------------------------
# Contest presets — canonical source of truth.
# These are editable starter presets, not final doctrine.
# ---------------------------------------------------------------------------
PRESETS = {
    "single_entry": {
        "uniqueness_min": 2,
        "captain_viability_pctile": 55,
        "captain_relative_floor": 0.78,
        "min_stack_size": 3,
        "allow_double_pitcher": False,
        "max_opposing_hitters_vs_pitcher": 1,
        "leverage_weight": 0.50,
        "min_salary_spend": 48500,
        "max_salary_left_for_bonus": 1200,
        "salary_leftover_bonus_strength": 0.50,
        "expected_salary_band_min": 48500,
        "expected_salary_band_max": 50000,
        "hitter_captain_preference": 1.15,
        "portfolio_overlap_penalties": DEFAULT_PORTFOLIO_OVERLAP_PENALTIES,
        "max_captain_exposure": 1.00,
        "top_review_count": 10,
        "review_min_distinct_captains": 2,
        "review_max_same_captain": 4,
        "review_same_captain_penalty": 2.50,
        "review_same_script_penalty": 0.75,
        "script_weights_override": {
            "pitcher_duel": 0.24,
            "fav_controls": 0.35,
            "slugfest": 0.20,
            "underdog": 0.14,
            "wraparound": 0.07,
        },
    },
    "three_max": {
        "uniqueness_min": 2,
        "captain_viability_pctile": 45,
        "captain_relative_floor": 0.72,
        "min_stack_size": 3,
        "allow_double_pitcher": False,
        "max_opposing_hitters_vs_pitcher": 2,
        "leverage_weight": 0.50,
        "min_salary_spend": 48000,
        "max_salary_left_for_bonus": 1600,
        "salary_leftover_bonus_strength": 0.75,
        "expected_salary_band_min": 48000,
        "expected_salary_band_max": 50000,
        "hitter_captain_preference": 1.10,
        "portfolio_overlap_penalties": DEFAULT_PORTFOLIO_OVERLAP_PENALTIES,
        "max_captain_exposure": 0.50,
        "script_weights_override": {
            "pitcher_duel": 0.16,
            "fav_controls": 0.31,
            "slugfest": 0.25,
            "underdog": 0.18,
            "wraparound": 0.10,
        },
    },
    "large_mme": {
        "uniqueness_min": 2,
        "captain_viability_pctile": 30,
        "captain_relative_floor": 0.60,
        "min_stack_size": 3,
        "allow_double_pitcher": True,
        "max_opposing_hitters_vs_pitcher": 2,
        "leverage_weight": 0.50,
        "min_salary_spend": 0,
        "max_salary_left_for_bonus": 3000,
        "salary_leftover_bonus_strength": 1.00,
        "expected_salary_band_min": 0,
        "expected_salary_band_max": 50000,
        "hitter_captain_preference": 1.05,
        "portfolio_overlap_penalties": DEFAULT_PORTFOLIO_OVERLAP_PENALTIES,
        "max_captain_exposure": 0.25,
        "script_weights_override": {
            "pitcher_duel": 0.12,
            "fav_controls": 0.24,
            "slugfest": 0.24,
            "underdog": 0.22,
            "wraparound": 0.18,
        },
    },
}
