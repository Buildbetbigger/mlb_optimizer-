"""Platform rules for MLB Showdown / Single-Game optimization.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, sections
"Platform Model", "Platform Scoring Reference", and "Platform constants".

Multiplier integrity rule
-------------------------
The multiplier slot must be audited explicitly in both optimization and
display logic. For DraftKings Showdown, the app must enforce and verify:

    captain_salary     = base_salary     * multiplier_salary_factor
    captain_projection = base_projection * multiplier_points_factor
    captain_ceiling    = base_ceiling    * multiplier_points_factor

The multiplier must be applied consistently in feasibility checks, optimizer
objective calculations, lineup salary totals, and lineup projection/ceiling
totals. If a lineup explanation or export view shows a Captain slot, the
displayed salary and projected points must already reflect the multiplier.
"""

from dataclasses import dataclass, field
from typing import Tuple

from utils.constants import (
    DK_SHOWDOWN_SALARY_CAP,
    FD_SINGLE_GAME_SALARY_CAP,
)


@dataclass(frozen=True)
class PlatformConfig:
    name: str
    roster_size: int
    multiplier_label: str
    multiplier_points_factor: float
    multiplier_salary_factor: float
    salary_cap: int
    requires_one_per_team: bool
    export_headers: Tuple[str, ...] = field(default_factory=tuple)


DK_SHOWDOWN = PlatformConfig(
    name="DK_SHOWDOWN",
    roster_size=6,
    multiplier_label="CPT",
    multiplier_points_factor=1.5,
    multiplier_salary_factor=1.5,
    salary_cap=DK_SHOWDOWN_SALARY_CAP,
    requires_one_per_team=True,
    export_headers=("CPT", "UTIL", "UTIL", "UTIL", "UTIL", "UTIL"),
)


FD_SINGLE_GAME = PlatformConfig(
    name="FD_SINGLE_GAME",
    roster_size=6,
    multiplier_label="MVP",
    multiplier_points_factor=1.5,
    multiplier_salary_factor=1.0,
    salary_cap=FD_SINGLE_GAME_SALARY_CAP,
    requires_one_per_team=True,
    export_headers=("MVP", "UTIL", "UTIL", "UTIL", "UTIL", "UTIL"),
)


# ---------------------------------------------------------------------------
# MLB scoring tables (Platform Scoring Reference section).
# Centralized here so ceiling logic, explanations, and simulations all read
# from the same source rather than burying values in prose.
# ---------------------------------------------------------------------------
DK_MLB_SCORING = {
    "single": 3,
    "double": 5,
    "triple": 8,
    "home_run": 10,
    "rbi": 2,
    "run": 2,
    "bb": 2,
    "hbp": 2,
    "sb": 5,
    "pitcher_ip": 2.25,
    "pitcher_k": 2,
    "pitcher_win": 4,
    "pitcher_er": -2,
    "pitcher_hit": -0.6,
    "pitcher_bb": -0.6,
}


FD_MLB_SCORING = {
    "single": 3,
    "double": 6,
    "triple": 9,
    "home_run": 12,
    "rbi": 3.5,
    "run": 3.2,
    "bb": 3,
    "hbp": 3,
    "sb": 6,
    "pitcher_ip": 3,
    "pitcher_k": 3,
    "pitcher_win": 6,
    "pitcher_er": -3,
    "pitcher_hit": 0,
    "pitcher_bb": 0,
}


_PLATFORMS = {
    DK_SHOWDOWN.name: DK_SHOWDOWN,
    FD_SINGLE_GAME.name: FD_SINGLE_GAME,
}


_SCORING_TABLES = {
    DK_SHOWDOWN.name: DK_MLB_SCORING,
    FD_SINGLE_GAME.name: FD_MLB_SCORING,
}


def get_platform(name: str) -> PlatformConfig:
    """Return the PlatformConfig for the given platform name."""
    try:
        return _PLATFORMS[name]
    except KeyError as exc:
        valid = ", ".join(sorted(_PLATFORMS))
        raise ValueError(
            f"Unknown platform '{name}'. Valid platforms: {valid}"
        ) from exc


def get_scoring_table(platform_name: str) -> dict:
    """Return the MLB scoring constants dict for the given platform name."""
    try:
        return _SCORING_TABLES[platform_name]
    except KeyError as exc:
        valid = ", ".join(sorted(_SCORING_TABLES))
        raise ValueError(
            f"Unknown platform '{platform_name}'. Valid platforms: {valid}"
        ) from exc
