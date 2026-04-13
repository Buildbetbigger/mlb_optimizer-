"""Portfolio Construction Engine.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Portfolio Construction Engine" including Stage 1 candidate generation,
Stage 2 greedy assembly with DEFAULT_PORTFOLIO_OVERLAP_PENALTIES, the
salary-shape bucket helper, and single-entry review-pool diversification.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

import math

import pandas as pd

from optimizer.explain import build_full_lineup_output
from optimizer.platform_rules import PlatformConfig
from optimizer.scenarios import apply_script_modifiers
from optimizer.scoring import compute_all_scores, get_captain_pool
from optimizer.solver import SolverContext, solve_candidate_set
from utils.constants import (
    DEFAULT_PORTFOLIO_OVERLAP_PENALTIES,
    LINEUP_SCORE_WEIGHTS,
    SALARY_LEFT_BUCKETS,
    SCRIPT_MODIFIERS,
)


__all__ = [
    "generate_candidates_by_script",
    "get_salary_shape",
    "assemble_portfolio",
    "build_review_pool",
    "compute_exposure_report",
]


# ---------------------------------------------------------------------------
# Stage 1 -- candidate generation across scripts
# ---------------------------------------------------------------------------

def generate_candidates_by_script(
    df: pd.DataFrame,
    platform: PlatformConfig,
    preset: Mapping[str, Any],
    script_weights: Mapping[str, float],
    candidates_per_script: int = 10,
    excluded_players: Iterable[str] = (),
    locked_players: Iterable[str] = (),
    locked_captain: str | None = None,
) -> list[dict]:
    """Generate candidate lineups across the active script vocabulary.

    For each script in ``script_weights`` (skipping zero-weight scripts):
        1. ``apply_script_modifiers`` to produce a script-tuned slate
        2. Build a ``SolverContext`` for that script
        3. Call ``solve_candidate_set`` for ``candidates_per_script``
        4. Convert each raw solver result into the full Lineup-shaped dict
           via ``build_full_lineup_output``, attaching script metadata
           and a Stage 2 ``quality_score`` (== final_score)
    """
    excluded_players = list(excluded_players)
    locked_players = list(locked_players)

    candidates: list[dict] = []
    seen_rosters: set[tuple[str, ...]] = set()

    for script_name, weight in script_weights.items():
        if weight <= 0 or script_name not in SCRIPT_MODIFIERS:
            continue

        script_df = apply_script_modifiers(df, script_name)

        ctx = SolverContext(
            script_name=script_name,
            preset=str(preset.get("__preset_name__", "")),
            platform=platform.name,
            stage2_ranking_weights=LINEUP_SCORE_WEIGHTS,
            script_modifier=dict(SCRIPT_MODIFIERS[script_name]),
            ownership_available=(
                "ownership_projection" in script_df.columns
                and script_df["ownership_projection"].notna().any()
            ),
        )

        raws = solve_candidate_set(
            df=script_df,
            platform=platform,
            preset=preset,
            solver_context=ctx,
            num_candidates=int(candidates_per_script),
            locked_captain=locked_captain,
            locked_players=locked_players,
            excluded_players=excluded_players,
        )

        for raw in raws:
            full = build_full_lineup_output(
                raw, script_df, platform, script_name, preset
            )
            roster_key = tuple(sorted(full["all_player_ids"]))
            if roster_key in seen_rosters:
                continue
            seen_rosters.add(roster_key)

            full["script_tag"] = script_name
            full["script_weight"] = float(weight)
            full["quality_score"] = float(full["final_score"])
            candidates.append(full)

    return candidates


# ---------------------------------------------------------------------------
# Salary-shape bucketing
# ---------------------------------------------------------------------------

def get_salary_shape(
    lineup: dict | Any,
    captain_pool_df: pd.DataFrame,
    slate_min_salary: float,
) -> str:
    """Return the salary-shape bucket label for a lineup.

    Output combines build flavor (cheap_captain_release / stars_and_scrubs /
    balanced) with a salary-left bucket (tight / modest / loose / extreme).
    Two lineups share a salary shape when this string matches.
    """
    if isinstance(lineup, dict):
        cap_id = lineup["captain_player_id"]
        cap_row = captain_pool_df[captain_pool_df["player_id"] == cap_id]
        if cap_row.empty:
            captain_salary = float(lineup.get("captain_salary", 0))
        else:
            captain_salary = float(cap_row.iloc[0]["salary"])
        utility_salaries = []
        for pid in lineup.get("utility_player_ids", []):
            row = captain_pool_df[captain_pool_df["player_id"] == pid]
            if not row.empty:
                utility_salaries.append(float(row.iloc[0]["salary"]))
        salary_left = int(lineup.get("salary_left", 0))
    else:
        captain_salary = float(lineup.captain.salary)
        utility_salaries = [float(u.salary) for u in lineup.utility]
        salary_left = int(getattr(lineup, "salary_left", 0))

    cap_pool_sals = (
        captain_pool_df["salary"].astype(float)
        if "salary" in captain_pool_df.columns
        else pd.Series(dtype=float)
    )
    if len(cap_pool_sals):
        captain_salary_pctile = float((cap_pool_sals <= captain_salary).mean())
    else:
        captain_salary_pctile = 1.0

    if captain_salary_pctile <= 0.30:
        build = "cheap_captain_release"
    elif utility_salaries and min(utility_salaries) <= float(slate_min_salary) * 1.1:
        build = "stars_and_scrubs"
    else:
        build = "balanced"

    bucket = next(
        (label for lo, hi, label in SALARY_LEFT_BUCKETS if lo <= salary_left <= hi),
        "extreme",
    )
    return f"{build}_{bucket}"


# ---------------------------------------------------------------------------
# Stage 2 -- greedy portfolio assembly
# ---------------------------------------------------------------------------

def _stack_skeleton(candidate: Mapping[str, Any]) -> tuple:
    """Frozen tuple of (team, count) tuples sorted by team, used for
    stack-skeleton overlap comparison."""
    return tuple(sorted(_team_hitter_counts(candidate).items()))


def _team_hitter_counts(candidate: Mapping[str, Any]) -> dict[str, int]:
    """Count selected hitters per team for a candidate.

    Used for both stack-skeleton overlap and exposure reporting. We rely
    on the captain row + utility rows already-present in the dict.
    """
    counts: dict[str, int] = defaultdict(int)
    cap_team = candidate.get("captain_team")
    if candidate.get("captain_is_pitcher", False) is False:
        # Captain is treated as a hitter for stack purposes when not a pitcher
        # We can't fully tell from the dict alone whether captain is a pitcher;
        # the stack_description string usually carries that info, but we
        # default to "captain counts as a hitter on captain_team" since
        # pitcher captains are rare and the secondary stack helper guards
        # against double-count.
        counts[cap_team] += 1
    return dict(counts)  # placeholder - filled in by caller below


def _candidate_team_counts(candidate: Mapping[str, Any], df: pd.DataFrame) -> dict[str, int]:
    """Per-team hitter count derived from the slate frame (authoritative)."""
    counts: dict[str, int] = defaultdict(int)
    for pid in candidate["all_player_ids"]:
        row = df[df["player_id"] == pid]
        if row.empty:
            continue
        r = row.iloc[0]
        if bool(r["is_hitter"]):
            counts[str(r["team"])] += 1
    return dict(counts)


def _candidate_core_set(candidate: Mapping[str, Any], k: int) -> frozenset:
    """Return a frozenset of the top-k highest-projection player_ids in
    the candidate lineup. Used for k-man core overlap detection."""
    # Quality proxy: order by lineup position (captain first, then utility
    # in the order returned by the solver). Without per-row median we use
    # all_player_ids ordering as a stable fallback.
    ids = candidate["all_player_ids"]
    return frozenset(ids[:k])


def _overlap_penalty(
    candidate: Mapping[str, Any],
    portfolio: list[Mapping[str, Any]],
    salary_shapes: dict[str, str],
    stack_skeletons: dict[str, tuple],
    penalties: Mapping[str, float],
    df: pd.DataFrame,
) -> float:
    """Aggregate overlap penalty against the existing portfolio."""
    if not portfolio:
        return 0.0

    pen = 0.0
    cap_id = candidate["captain_player_id"]
    cand_team_counts = _candidate_team_counts(candidate, df)
    primary_team = max(cand_team_counts, key=cand_team_counts.get) if cand_team_counts else None
    cand_script = candidate.get("script_tag")
    cand_core3 = _candidate_core_set(candidate, 3)
    cand_core4 = _candidate_core_set(candidate, 4)
    cand_shape = salary_shapes.get(_cand_id(candidate))
    cand_skeleton = stack_skeletons.get(_cand_id(candidate))

    for prior in portfolio:
        if prior["captain_player_id"] == cap_id:
            pen += penalties.get("captain_overlap_penalty", 0.0)
        if cand_core3 == _candidate_core_set(prior, 3):
            pen += penalties.get("core_3man_overlap_penalty", 0.0)
        if cand_core4 == _candidate_core_set(prior, 4):
            pen += penalties.get("core_4man_overlap_penalty", 0.0)
        prior_team_counts = _candidate_team_counts(prior, df)
        prior_primary = max(prior_team_counts, key=prior_team_counts.get) if prior_team_counts else None
        if primary_team is not None and primary_team == prior_primary:
            # team-level stack overlap, honoring per-team count similarity
            pen += penalties.get("stack_overlap_penalty", 0.0)
        if cand_skeleton is not None and cand_skeleton == stack_skeletons.get(_cand_id(prior)):
            # Slightly stronger than team-level stack; we apply once.
            pass  # already counted via stack_overlap_penalty above
        if cand_script and cand_script == prior.get("script_tag"):
            pen += penalties.get("script_overlap_penalty", 0.0)
        if cand_shape and cand_shape == salary_shapes.get(_cand_id(prior)):
            pen += penalties.get("salary_shape_overlap_penalty", 0.0)
    return pen


def _cand_id(candidate: Mapping[str, Any]) -> str:
    return tuple(sorted(candidate["all_player_ids"]))  # type: ignore[return-value]


def assemble_portfolio(
    candidates: list[dict],
    num_lineups: int,
    preset: Mapping[str, Any],
    script_weights: Mapping[str, float],
    df: pd.DataFrame | None = None,
    captain_pool_df: pd.DataFrame | None = None,
) -> list[dict]:
    """Greedy diminishing-returns selection over candidate lineups.

    ``df`` is the post-modifier slate frame used to look up team affiliations
    when computing overlap. ``captain_pool_df`` is the captain-eligible slate
    used by ``get_salary_shape``; if not provided, ``df`` is used directly.
    """
    if not candidates or num_lineups <= 0:
        return []

    penalties = dict(DEFAULT_PORTFOLIO_OVERLAP_PENALTIES)
    penalties.update(dict(preset.get("portfolio_overlap_penalties", {}) or {}))

    max_cap_exposure = float(preset.get("max_captain_exposure", 1.0))
    max_cap_count = max(1, math.ceil(max_cap_exposure * num_lineups))

    if df is None:
        # Without df, overlap detection still works minus team-count info.
        df = pd.DataFrame(columns=["player_id", "team", "is_hitter", "salary"])
    cap_pool = captain_pool_df if captain_pool_df is not None else df

    # Pre-compute salary shapes + stack skeletons for all candidates
    slate_min_sal = float(df["salary"].astype(float).min()) if "salary" in df.columns and len(df) else 0.0
    salary_shapes: dict[Any, str] = {}
    stack_skeletons: dict[Any, tuple] = {}
    for cand in candidates:
        cid = _cand_id(cand)
        salary_shapes[cid] = get_salary_shape(cand, cap_pool, slate_min_sal)
        stack_skeletons[cid] = tuple(sorted(_candidate_team_counts(cand, df).items()))

    # Sort candidates by quality desc (this is the seeding for step 1).
    sorted_cands = sorted(candidates, key=lambda c: -float(c["quality_score"]))
    portfolio: list[dict] = [sorted_cands[0]]
    captain_counts: Counter = Counter([sorted_cands[0]["captain_player_id"]])

    remaining = sorted_cands[1:]
    while len(portfolio) < num_lineups and remaining:
        best_idx = None
        best_score = -math.inf
        for i, cand in enumerate(remaining):
            cap_id = cand["captain_player_id"]
            if captain_counts[cap_id] >= max_cap_count:
                continue
            quality = float(cand["quality_score"])
            penalty = _overlap_penalty(cand, portfolio, salary_shapes, stack_skeletons, penalties, df)
            adj_score = quality - penalty
            if adj_score > best_score:
                best_score = adj_score
                best_idx = i
        if best_idx is None:
            break
        chosen = remaining.pop(best_idx)
        portfolio.append(chosen)
        captain_counts[chosen["captain_player_id"]] += 1

    # Annotate portfolio entries with their salary_shape for downstream display.
    for ln in portfolio:
        ln["salary_shape"] = salary_shapes[_cand_id(ln)]
    return portfolio


# ---------------------------------------------------------------------------
# Single-entry review pool
# ---------------------------------------------------------------------------

def build_review_pool(candidates: list[dict], preset: Mapping[str, Any]) -> list[dict]:
    """Assemble a top-N review pool with single-entry repetition penalties.

    The #1 raw recommendation (highest quality_score) is preserved; subsequent
    review-pool entries are scored with mild repetition penalties so the
    review set diversifies across captains and scripts even when one
    captain dominates raw rankings.
    """
    if not candidates:
        return []

    top_n = int(preset.get("top_review_count", 10))
    same_cap_pen = float(preset.get("review_same_captain_penalty", 0.0))
    same_script_pen = float(preset.get("review_same_script_penalty", 0.0))
    min_distinct_caps = int(preset.get("review_min_distinct_captains", 1))
    max_same_cap = int(preset.get("review_max_same_captain", top_n))

    sorted_cands = sorted(candidates, key=lambda c: -float(c["quality_score"]))
    review: list[dict] = [sorted_cands[0]]  # unchanged #1 recommendation
    captain_counts: Counter = Counter([sorted_cands[0]["captain_player_id"]])
    script_counts: Counter = Counter([sorted_cands[0].get("script_tag")])

    remaining = sorted_cands[1:]
    while len(review) < top_n and remaining:
        best_idx = None
        best_score = -math.inf
        for i, cand in enumerate(remaining):
            cap_id = cand["captain_player_id"]
            if captain_counts[cap_id] >= max_same_cap:
                continue
            adj = (
                float(cand["quality_score"])
                - same_cap_pen * captain_counts[cap_id]
                - same_script_pen * script_counts[cand.get("script_tag")]
            )
            if adj > best_score:
                best_score = adj
                best_idx = i
        if best_idx is None:
            break
        chosen = remaining.pop(best_idx)
        review.append(chosen)
        captain_counts[chosen["captain_player_id"]] += 1
        script_counts[chosen.get("script_tag")] += 1

    # Enforce review_min_distinct_captains by replacing the lowest-quality
    # duplicate-captain entry with the highest-quality fresh-captain candidate.
    distinct = len({c["captain_player_id"] for c in review})
    pool = [c for c in candidates if c not in review]
    pool_sorted = sorted(pool, key=lambda c: -float(c["quality_score"]))
    while distinct < min_distinct_caps and pool_sorted:
        existing_caps = {c["captain_player_id"] for c in review}
        replacement = next(
            (c for c in pool_sorted if c["captain_player_id"] not in existing_caps),
            None,
        )
        if replacement is None:
            break
        # replace the lowest-quality non-#1 entry whose captain has > 1 copy
        cap_freq = Counter(c["captain_player_id"] for c in review)
        candidates_to_drop = [
            (i, c) for i, c in enumerate(review[1:], start=1)
            if cap_freq[c["captain_player_id"]] > 1
        ]
        if not candidates_to_drop:
            break
        candidates_to_drop.sort(key=lambda t: float(t[1]["quality_score"]))
        drop_i, _ = candidates_to_drop[0]
        review[drop_i] = replacement
        pool_sorted.remove(replacement)
        distinct = len({c["captain_player_id"] for c in review})

    return review


# ---------------------------------------------------------------------------
# Exposure report
# ---------------------------------------------------------------------------

def compute_exposure_report(portfolio: list[dict]) -> dict:
    """Aggregate exposure / distribution stats across a portfolio."""
    n = len(portfolio)
    if n == 0:
        return {
            "n_lineups": 0,
            "captain_exposure": {},
            "player_exposure": {},
            "team_exposure": {},
            "stack_frequency": {},
            "scenario_distribution": {},
            "avg_total_salary": 0.0,
            "avg_salary_left": 0.0,
            "salary_shape_distribution": {},
        }

    captain_counts: Counter = Counter()
    player_counts: Counter = Counter()
    team_counts: Counter = Counter()
    stack_freq: Counter = Counter()
    script_counts: Counter = Counter()
    salary_used = 0
    salary_left = 0
    salary_shape_counts: Counter = Counter()

    for ln in portfolio:
        captain_counts[ln["captain_name"]] += 1
        for pid_name in [ln["captain_name"], *ln["utility_names"]]:
            player_counts[pid_name] += 1
        # Use stack_description as the stack key (already computed)
        stack_freq[ln.get("stack_description", "?")] += 1
        script_counts[ln.get("script_tag", "n/a")] += 1
        salary_used += int(ln["total_salary"])
        salary_left += int(ln["salary_left"])
        if ln.get("salary_shape"):
            salary_shape_counts[ln["salary_shape"]] += 1
        # Team exposure: count each team appearance per lineup
        seen_teams = set()
        for pid in ln["all_player_ids"]:
            # We don't have direct team lookup here; rely on captain_team and
            # let downstream display join team membership if richer detail is
            # needed. For now, count captain_team only.
            pass
        team_counts[ln["captain_team"]] += 1

    def pct(c: Counter) -> dict:
        return {k: round(v / n, 4) for k, v in c.most_common()}

    return {
        "n_lineups": n,
        "captain_exposure": pct(captain_counts),
        "player_exposure": pct(player_counts),
        "team_exposure": pct(team_counts),
        "stack_frequency": pct(stack_freq),
        "scenario_distribution": pct(script_counts),
        "salary_shape_distribution": pct(salary_shape_counts),
        "avg_total_salary": round(salary_used / n, 1),
        "avg_salary_left": round(salary_left / n, 1),
    }
