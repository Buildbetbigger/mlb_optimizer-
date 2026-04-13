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
    export_headers=("CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"),
)


FD_SINGLE_GAME = PlatformConfig(
    name="FD_SINGLE_GAME",
    roster_size=6,
    multiplier_label="MVP",
    multiplier_points_factor=1.5,
    multiplier_salary_factor=1.0,
    salary_cap=FD_SINGLE_GAME_SALARY_CAP,
    requires_one_per_team=True,
    export_headers=("MVP", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"),
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


# ---------------------------------------------------------------------------
# Export Requirements (blueprint section "Export Requirements")
# ---------------------------------------------------------------------------
#
# Rules:
# - export format is platform-specific and lives here in platform_rules.py
# - export headers are pinned in PlatformConfig.export_headers
# - export validation runs before download
# - export uses the exact identifier and column order expected by the site;
#   for V1 we export player names, which DK accepts and which keeps the
#   pipeline self-contained until DK player-id columns are wired up
# ---------------------------------------------------------------------------

import csv as _csv
import io as _io


def format_export_row(lineup_dict, platform: "PlatformConfig") -> dict:
    """Return a header-keyed dict for one lineup row.

    The captain slot uses ``platform.export_headers[0]`` (e.g. ``"CPT"`` /
    ``"MVP"``) and the remaining five slots use ``"FLEX"``. Duplicate
    "FLEX" keys are NOT collapsed - this function returns an OrderedDict-
    style list of (header, value) pairs as a regular dict only when the
    caller passes a single header per slot. For CSV writing, prefer
    ``export_lineups_csv`` which iterates positionally.
    """
    headers = platform.export_headers
    captain_name = lineup_dict.get("captain_name", "")
    util_names = list(lineup_dict.get("utility_names", []))
    values = [captain_name, *util_names]
    if len(values) != platform.roster_size:
        raise ValueError(
            f"Lineup has {len(values)} players; platform expects "
            f"{platform.roster_size}"
        )
    # Returned dict uses positional index suffixes so duplicate header
    # labels like "FLEX" don't collide as dict keys. The CSV writer
    # uses headers list directly and ignores these suffixes.
    return {f"{h}_{i}": v for i, (h, v) in enumerate(zip(headers, values))}


def validate_export(lineups: list[dict], platform: "PlatformConfig") -> list[str]:
    """Pre-flight validation before writing a CSV.

    Returns a list of human-readable issues; empty == ready to download.
    """
    errors: list[str] = []
    if not lineups:
        errors.append("No lineups to export.")
        return errors

    expected_size = platform.roster_size
    headers = platform.export_headers
    if len(headers) != expected_size:
        errors.append(
            f"Platform '{platform.name}' has {len(headers)} export "
            f"header(s) but roster_size = {expected_size}."
        )
    if headers[0] != platform.multiplier_label:
        errors.append(
            f"Export header[0] is '{headers[0]}' but platform "
            f"multiplier_label is '{platform.multiplier_label}'."
        )

    for i, ln in enumerate(lineups, start=1):
        missing_fields = [
            f for f in ("captain_name", "utility_names", "total_salary")
            if f not in ln
        ]
        if missing_fields:
            errors.append(
                f"Lineup #{i} is missing fields: {', '.join(missing_fields)}"
            )
            continue

        slots = [ln["captain_name"], *ln["utility_names"]]
        if len(slots) != expected_size:
            errors.append(
                f"Lineup #{i} has {len(slots)} players; expected "
                f"{expected_size}."
            )
            continue
        if any(not (s and str(s).strip()) for s in slots):
            errors.append(f"Lineup #{i} has a blank player name in a slot.")
        if len(set(slots)) != len(slots):
            errors.append(f"Lineup #{i} has a duplicated player.")

        if "total_salary" in ln and int(ln["total_salary"]) > platform.salary_cap:
            errors.append(
                f"Lineup #{i} total salary ${int(ln['total_salary']):,} "
                f"exceeds cap ${platform.salary_cap:,}."
            )

    return errors


def export_lineups_csv(lineups: list[dict], platform: "PlatformConfig") -> str:
    """Return a CSV string with the platform's pinned headers.

    Raises ValueError if ``validate_export`` reports any issues - callers
    should run validation first to surface a clean diagnostic to the user
    rather than relying on an exception path.
    """
    issues = validate_export(lineups, platform)
    if issues:
        raise ValueError(
            "Export is not valid:\n  - " + "\n  - ".join(issues)
        )

    buf = _io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow(list(platform.export_headers))

    for ln in lineups:
        slots = [ln["captain_name"], *ln["utility_names"]]
        writer.writerow(slots)

    return buf.getvalue()
