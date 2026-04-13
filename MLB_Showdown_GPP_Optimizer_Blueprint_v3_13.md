# MLB Showdown GPP Optimizer — Refined App Blueprint v3.13

## Purpose

Build a Streamlit-based MLB showdown optimizer that behaves like an expert tournament strategist, not a thin wrapper around a projection sheet.

The app must do six things well:

1. interpret betting context
2. understand showdown correlation
3. rank hitters and pitchers by different logic
4. rank captain and utility roles differently
5. generate differentiated multi-lineup portfolios, not just valid individual lineups
6. explain why each lineup exists

This blueprint is written against the actual cheat-sheet schema currently available:

- `first_name`
- `last_name`
- `position`
- `injury_status`
- `game_date`
- `slate`
- `team`
- `opp`
- `confirmed_order`
- `starting_pitcher`
- `hand`
- `spread`
- `over_under`
- `implied_team_score`
- `salary`
- `L5_fppg_avg`
- `L10_fppg_avg`
- `szn_fppg_avg`
- `ppg_projection`
- `value_projection`
- `ownership_projection`

`ownership_projection` is **optional**, not required.

---

## Core Product Principle

The app should follow a strict rule:

- when ownership exists, use it
- when ownership is blank, do not fake it
- when ownership is absent, redefine leverage as **structural differentiation**

Tournament strength comes from the combination of:

- projection quality
- ceiling
- correlation
- scenario fit
- captain viability
- portfolio differentiation
- lineup coherence

The optimizer must never confuse “different” with “good.”

---

## Platform Model

Platform support should be expressed through explicit config objects, not scattered conditionals.

### Platform constants

```python
DK_SHOWDOWN_SALARY_CAP = 50000
FD_SINGLE_GAME_SALARY_CAP = 60000
```

### DraftKings Showdown
- 6 total players
- 1 Captain
- Captain points = 1.5x
- Captain salary = 1.5x
- salary cap = `DK_SHOWDOWN_SALARY_CAP`

### FanDuel Single-Game / Showdown-style support
- 6 total players
- 1 multiplier slot
- salary cap = `FD_SINGLE_GAME_SALARY_CAP`
- roster rules isolated in one platform module so the UI and solver can switch formats cleanly

### Platform config example

```python
@dataclass(frozen=True)
class PlatformConfig:
    name: str
    roster_size: int
    multiplier_label: str
    multiplier_points_factor: float
    multiplier_salary_factor: float
    salary_cap: int
    requires_one_per_team: bool
    export_headers: list[str]
```

All UI logic, solver logic, export logic, and explanation logic should read from `PlatformConfig`, not from hard-coded platform checks scattered through the app.

### Multiplier integrity rule
The multiplier slot must be audited explicitly in both optimization and display logic.

For DraftKings Showdown, the app must enforce and verify:
- `captain_salary = base_salary * multiplier_salary_factor`
- `captain_projection = base_projection * multiplier_points_factor`
- `captain_ceiling = base_ceiling * multiplier_points_factor`

The app should include a regression test that confirms the multiplier is applied consistently in:
- feasibility checks
- optimizer objective calculations
- lineup table salary totals
- lineup table projection and ceiling totals

If a lineup explanation or export view shows a Captain slot, the displayed salary and projected points must already reflect the multiplier.

---

## Platform Scoring Reference

The app should centralize platform scoring rules in one place so ceiling logic, player explanations, and simulations are grounded in real scoring instead of vague baseball language.

### DraftKings MLB showdown scoring reference
At minimum, the platform module should define constants for:
- single
- double
- triple
- home run
- RBI
- run
- walk / HBP if applicable to platform scoring
- stolen base
- pitcher inning pitched
- strikeout
- win
- earned run allowed
- hit allowed
- walk allowed
- complete game / shutout bonuses if relevant

### FanDuel single-game scoring reference
Define the corresponding constants in the same module.

### Rule
Do not bury scoring values in prose or explanations. The blueprint, simulation layer, and explanation layer should all read from the same scoring table in `platform_rules.py`.

---

## Design Principles

1. **Do not optimize median only.**  
   Showdown tournaments are won by ceiling and correlation, not by safest median lineup.

2. **Do not score hitters and pitchers the same way.**  
   Their paths to ceiling are different.

3. **Do not treat ownership as mandatory.**  
   The optimizer must remain fully usable without it.

4. **Do not let uniqueness overwhelm quality.**  
   Uniqueness is only useful after lineup coherence is established.

5. **Do not make the app a black box.**  
   Every lineup should come with a readable explanation.

6. **Do not assume the “best lineup” is singular.**  
   The app should build portfolios across plausible game scripts.

7. **Do not generate portfolios by accident.**  
   Portfolio construction is its own optimization problem, not just single-lineup optimization repeated with a uniqueness rule.

8. **Do not let one scoring component dominate because of scale.**  
   All major lineup-score components must be normalized before weighting.

---

## Data Input — What Each Column Actually Does

| Column | Strategic role |
|---|---|
| `spread` | Favorite/underdog context. Supports pitcher viability, favorite stacks, and script weighting |
| `over_under` | Game-wide run environment. Higher totals raise hitter concentration and reduce double-pitcher viability |
| `implied_team_score` | Team-specific run expectation. Strongest stack-level input in the file |
| `confirmed_order` | Order context and adjacency correlation. Critical for top-order stacks and wraparound value stacks |
| `starting_pitcher` | Lets the app identify the opposing starter and infer platoon context |
| `hand` | Used for platoon logic, both hitter-vs-pitcher and pitcher-vs-lineup summaries |
| `L5_fppg_avg`, `L10_fppg_avg`, `szn_fppg_avg` | Recent-form context. Useful but capped so it never overwhelms season-level signal |
| `ppg_projection` | Baseline projection anchor |
| `value_projection` | Salary efficiency and captain affordability input |
| `salary` | Roster construction and uniqueness via salary-leftover logic |
| `ownership_projection` | Optional. If present, use it. If blank, hide ownership-specific controls and use structural uniqueness instead |

---

## Data Preparation Layer

### 1. Normalize identity
Create:
- `player_name = first_name + " " + last_name`
- `player_id` as a stable row identifier

### 2. Normalize role
Create:
- `is_pitcher`
- `is_hitter`
- `is_starting_pitcher`

Pitchers should have `batting_order = 0`.

### 3. Normalize batting order
- convert `confirmed_order` to integer when present
- missing order for hitters should trigger a warning
- missing order should not crash the slate; it should default to conservative order handling

### 4. Infer opposing pitcher hand
For each hitter:
- identify the opposing starting pitcher row
- copy that pitcher’s hand into `opp_pitcher_hand`

### 5. Create platoon flags
For hitters:
- favorable if L vs R or R vs L
- neutral if same-handed
- switch-hitters (`hand == "S"`) default to favorable against either pitcher hand for Version 1

Default rule:

```python
if hitter_hand == "S":
    platoon_edge = "favorable"
elif hitter_hand != opp_pitcher_hand:
    platoon_edge = "favorable"
else:
    platoon_edge = "neutral"
```

### 6. Create game metadata
For each player:
- `game_total = over_under`
- `team_total = implied_team_score`
- `is_favorite = spread < 0`
- `is_underdog = spread > 0`

### 7. Create restrained form deltas

Make the clip bounds configurable rather than hard-coded.

```python
L5_FORM_CLIP = 0.12
L10_FORM_CLIP = 0.08
FORM_SIGNAL_CLIP = 0.10

l5_delta = clip((L5_fppg_avg - szn_fppg_avg) / max(szn_fppg_avg, 1), -L5_FORM_CLIP, L5_FORM_CLIP)
l10_delta = clip((L10_fppg_avg - szn_fppg_avg) / max(szn_fppg_avg, 1), -L10_FORM_CLIP, L10_FORM_CLIP)

form_signal = 0.55 * l5_delta + 0.45 * l10_delta
form_signal = clip(form_signal, -FORM_SIGNAL_CLIP, FORM_SIGNAL_CLIP)
```

Form is a modifier, not a replacement for projection.

### 8. Create slate-quality metadata
Track:
- missing batting orders
- inferred pitcher-hand confidence
- injury/scratch risk
- platform eligibility
- data freshness timestamp

The app should expose slate confidence, not just lineups.

---

## Normalization Strategy

Showdown slates are tiny. Standard z-scores can become noisy when the player pool is only 18 to 24 players.

### Rule
- use **rank-based percentile normalization** when slate size < 30 players
- use **robust z-score or percentile normalization** when slate size is 30 to 60
- use **standard z-score normalization** only on larger pools

### Example helper

```python
def normalize_feature(values, slate_size):
    if slate_size < 30:
        return rank_percentile(values)
    elif slate_size <= 60:
        return robust_zscore(values)
    return standard_zscore(values)
```

This rule applies to:
- implied team score context
- over/under context
- salary efficiency
- lineup-level score normalization

### Two-team showdown note
On a two-team showdown slate, `normalize_feature(implied_team_score, slate_size)` will usually collapse into a team-level toggle rather than a player-level gradient. That is acceptable. In practice, `team_total_adj` functions primarily as a side-of-game context bonus: players on the higher-implied team receive the stronger team-total context, while players on the lower-implied team receive the weaker one.

Do not assume standard z-scores are stable on single-game slates.

---

## Scoring Architecture

The optimizer should track multiple scores rather than collapse everything into one early number:

- `median_score`
- `ceiling_score`
- `captain_score`
- `correlation_score`
- `uniqueness_score`
- `coherence_score`
- `final_optimizer_score`

### Critical scoring rule: normalize components before weighting

Projection, ceiling, correlation, uniqueness, and conflict terms must be normalized to a common scale before combining them. Otherwise one term can dominate purely because of magnitude.

Recommended approach:
- convert each lineup-level component to a bounded score such as 0 to 100
- or normalize components across candidate lineups, then clip extremes
- weights must be configurable but bounded

Example:

```python
LINEUP_SCORE_WEIGHTS = {
    "w_projection": 0.30,
    "w_ceiling": 0.30,
    "w_correlation": 0.20,
    "w_uniqueness": 0.05,
    "w_coherence": 0.10,
    "w_conflict": 0.05,
}

def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}

weights = normalize_weights(LINEUP_SCORE_WEIGHTS)

final_optimizer_score = (
    weights["w_projection"] * norm_projection
    + weights["w_ceiling"] * norm_ceiling
    + weights["w_correlation"] * norm_correlation
    + weights["w_uniqueness"] * norm_uniqueness
    + weights["w_coherence"] * norm_coherence
    - weights["w_conflict"] * norm_conflict
)
```

Rules:
- lineup-score weights must sum to `1.00`
- if a user edits weights, validate and renormalize automatically
- these normalized weighted scores are for **Stage 2 portfolio ranking and review-pool comparison**, not the raw Stage 1 lineup solver objective

### A. Hitter median score

```python
base = ppg_projection

order_adj_map = {
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
order_adj = order_adj_map.get(order, -0.05)

team_total_adj = 0.06 * normalize_feature(implied_team_score, slate_size)
game_total_adj = 0.03 * normalize_feature(over_under, slate_size)
value_adj = 0.03 * normalize_feature(value_projection, slate_size)
platoon_adj = 0.06 if favorable_platoon else 0.0

hitter_median_score = base * (
    1
    + form_signal
    + order_adj
    + team_total_adj
    + game_total_adj
    + value_adj
    + platoon_adj
)
```

### B. Pitcher median score

```python
base = ppg_projection

favorite_adj = 0.06 if is_favorite else -0.03
opp_team_total_adj = 0.10 * normalize_feature(-opp_implied_team_score, slate_size)
game_total_adj = 0.05 * normalize_feature(-over_under, slate_size)

pitcher_median_score = base * (
    1
    + form_signal
    + favorite_adj
    + opp_team_total_adj
    + game_total_adj
)
```

### C. Ceiling score

The CSV does not provide a true ceiling field, so the app must derive one.

Critical rule: do not let ceiling compound too aggressively on top of an already-adjusted median.

#### Hitter ceiling logic
- use a base ceiling anchor
- add only **upside-specific positive adjustments**
- cap the total ceiling multiplier

```python
upside_order_bonus = max(order_adj, 0)
upside_team_total_bonus = max(team_total_adj, 0)
upside_platoon_bonus = max(platoon_adj, 0)
upside_form_bonus = max(form_signal, 0)

hitter_ceiling_multiplier = min(
    1.18
    + upside_order_bonus
    + upside_team_total_bonus
    + upside_platoon_bonus
    + upside_form_bonus,
    1.40
)

hitter_ceiling_score = hitter_median_score * hitter_ceiling_multiplier
```

#### Pitcher ceiling logic

```python
pitcher_ceiling_multiplier = min(
    1.15
    + max(favorite_adj, 0)
    + max(opp_team_total_adj, 0)
    + max(form_signal, 0),
    1.35
)

pitcher_ceiling_score = pitcher_median_score * pitcher_ceiling_multiplier
```

### Ceiling boost rule
The Stage 1 solver should use **incremental upside** rather than the full ceiling score when adding an upside term to the raw objective.

```python
ceiling_boost = ceiling_score - median_score
```

Rule:
- `median_score` is the baseline projection layer
- `ceiling_boost` is the non-overlapping upside layer above median
- do not set `ceiling_boost = ceiling_score`, or median projection will be double-counted in the raw solver objective

### D. Captain score

```python
captain_score = ceiling_score * multiplier_slot_points_factor
```

If ownership exists:

```python
captain_score *= (1 / ownership_projection) ** leverage_weight
```

If ownership is absent, do not use that term.

### E. Captain viability floor

A cheap or contrarian captain must still clear a viability threshold.

#### Default captain viability rule
- captain must be at or above the preset-specific captain viability percentile in raw ceiling score among all players on the slate
- below that threshold, the player is excluded from the default captain pool unless manually locked by the user

#### Cheap-captain guardrail
A cheap captain should not enter the pool merely because it creates salary flexibility.

Recommended default:

```python
captain_relative_floor = captain_raw_ceiling / top_raw_captain_ceiling

# preset interpretation
# single_entry: captain_relative_floor >= 0.78
# three_max:    captain_relative_floor >= 0.72
# large_mme:    captain_relative_floor >= 0.60
```

A captain candidate should pass both:
- percentile-based captain viability
- relative raw-ceiling proximity to the slate leader

#### Additional default guards
- minimum team-total support can be applied as a secondary filter
- no bottom-order captain unless salary-release logic materially improves the lineup and the script supports it
- no cheap captain who cannot plausibly finish as highest scorer in the game script

Make these advanced settings, while defaulting from the selected contest preset:

```python
captain_viability_pctile = preset.captain_viability_pctile
captain_relative_floor = preset.captain_relative_floor
```

This protects the optimizer from fake-contrarian captain builds, especially in single-entry and three-max modes.

---

## Correlation Engine

Showdown is correlation-heavy. The lineup objective should reward groups of players that benefit from the same scoring events.

### Pairwise hitter bonuses
- same-team hitter pair: positive
- adjacent batting order: strong positive
- 3-4-5 or 1-2-3 clusters: positive
- favorable platoon hitter: individual positive
- wraparound adjacency such as 8-9-1: light positive if team total supports it

### Pitcher interaction rules
- pitcher + 2 to 4 hitters from same team: small positive
- pitcher + many opposing hitters: penalty
- opposing captain against selected pitcher: strong penalty by default
- one elite opposing run-back can still be viable
- double-pitcher gains value in low-total scripts only

### Suggested lineup-level bonuses

```python
same_team_bonus = 0.05
adjacent_order_bonus = 0.15
heart_of_order_bonus = 0.10
top_of_order_bonus = 0.10
wraparound_bonus = 0.05
favorable_platoon_bonus = 0.06
pitcher_conflict_penalty = -0.12
opposing_captain_penalty = -0.20
```

These are **percentage weights**, not raw points. They must be converted into point-scale bonuses before they are added to lineup score.

#### Point-scale alignment rule

Apply each pairwise bonus as a percentage of a point-scale base such as the average median score of the pair:

```python
pair_base_points = 0.5 * (player_i_median_score + player_j_median_score)
pair_bonus_points = pair_base_points * pair_bonus_weight
```

This keeps pairwise correlation on the same scale as projection and ceiling terms.

#### Captain-correlation amplification

When one player in the pair is the Captain / multiplier-slot player, scale the captain-linked pair bonus by the platform scoring multiplier:

```python
if player_i_is_captain or player_j_is_captain:
    pair_bonus_points *= multiplier_points_factor
```

This reflects the fact that correlated captain events are more valuable in showdown because the captain's scoring events are worth more.

These should be configurable weights, not hard-coded doctrine.

---

## Double-Pitcher Rule

Do not leave “low-total script” vague. Define explicit defaults.

### Default double-pitcher policy
- **favored** when `over_under <= 7.5` and both pitchers are confirmed starters
- **neutral / optional** when `7.5 < over_under < 9.0`, depending on spread and implied team scores
- **discouraged or blocked by default** when `over_under >= 9.0`

### Context modifiers
Double-pitcher gains relative strength when:
- both implied team scores are modest
- both pitchers have positive projection profiles
- the game spread supports run suppression rather than a one-sided blowup

Double-pitcher loses strength when:
- one team total is significantly elevated
- both offenses project for strong top-order concentration
- the game projects as a slugfest

---

## Conflict Taxonomy

The app should classify conflicts explicitly rather than treat all conflicts as the same penalty.

### Conflict types

1. **Soft conflict**
   - pitcher plus one elite opposing run-back
   - expensive one-off outside primary stack but still script-consistent

2. **Medium conflict**
   - pitcher plus two opposing hitters
   - non-correlated premium bat that breaks stack concentration
   - double-pitcher in only moderately favorable scoring environments

3. **Hard conflict**
   - captain hitter directly opposing selected pitcher
   - pitcher plus opposing mini-stack
   - bottom-order punt captain with no script-based path
   - double-pitcher in a clear slugfest environment

### Conflict handling
- soft conflicts can remain with a modest penalty
- medium conflicts require a stronger penalty
- hard conflicts should usually be blocked or heavily penalized unless explicitly allowed by the user

This taxonomy should drive both the solver and the explanation layer.

Standardize conflict codes so the app does not depend on loose prose or ad hoc strings:

```python
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
```

Conflict objects should carry both a stable `code` and an explicit `severity` field rather than inferring severity from string parsing.

Define trigger rules explicitly so conflict assignment is consistent:

```python
def detect_conflicts(lineup, slate_ceiling_75th, slate_ceiling_40th) -> list[Conflict]:
    conflicts = []
    pitchers = [p for p in lineup.all_players if p.is_pitcher]
    captain = lineup.captain

    for pitcher in pitchers:
        opp_hitters = [
            p for p in lineup.all_players
            if p.is_hitter and p.team == pitcher.opp
        ]

        if len(opp_hitters) == 1 and opp_hitters[0].ceiling_score >= slate_ceiling_75th:
            conflicts.append(Conflict(
                code="PITCHER_SINGLE_RUNBACK",
                severity="soft",
                description=f"{pitcher.player_name} vs 1 elite runback {opp_hitters[0].player_name}",
                player_id=opp_hitters[0].player_id,
            ))

        if len(opp_hitters) == 1 and opp_hitters[0].ceiling_score < slate_ceiling_75th:
            conflicts.append(Conflict(
                code="PITCHER_SINGLE_RUNBACK_MINOR",
                severity="soft",
                description=f"{pitcher.player_name} vs 1 non-elite runback {opp_hitters[0].player_name}",
                player_id=opp_hitters[0].player_id,
            ))

        if len(opp_hitters) == 2:
            conflicts.append(Conflict(
                code="PITCHER_TWO_OPP_HITTERS",
                severity="medium",
                description=f"{pitcher.player_name} vs 2 opposing hitters",
            ))

        if len(opp_hitters) >= 3:
            conflicts.append(Conflict(
                code="PITCHER_OPP_MINISTACK",
                severity="hard",
                description=f"{pitcher.player_name} vs {len(opp_hitters)} opposing hitters",
            ))

        if captain.is_hitter and captain.team == pitcher.opp:
            conflicts.append(Conflict(
                code="CAPTAIN_VS_OWN_PITCHER",
                severity="hard",
                description=f"Captain {captain.player_name} opposes {pitcher.player_name}",
                player_id=captain.player_id,
            ))

    if len(pitchers) == 2:
        ou = lineup.all_players[0].over_under
        if ou >= 9.0:
            conflicts.append(Conflict(
                code="DOUBLE_PITCHER_SLUGFEST",
                severity="hard",
                description=f"Double pitcher in {ou} O/U environment",
            ))
        elif ou >= 7.5:
            conflicts.append(Conflict(
                code="DOUBLE_PITCHER_MODERATE",
                severity="medium",
                description=f"Double pitcher in {ou} O/U environment",
            ))

    if captain.is_hitter and captain.batting_order >= 7:
        if captain.ceiling_score < slate_ceiling_40th:
            conflicts.append(Conflict(
                code="BOTTOM_ORDER_CAPTAIN_NO_PATH",
                severity="hard",
                description=f"Captain {captain.player_name} (order {captain.batting_order}) below ceiling threshold",
                player_id=captain.player_id,
            ))

    return conflicts
```

Define the remaining premium-isolation conflicts explicitly so no dead codes remain in the vocabulary:

```python
def has_adjacent_teammate(player, lineup) -> bool:
    return any(
        p.is_hitter
        and p.team == player.team
        and p.player_id != player.player_id
        and abs(p.batting_order - player.batting_order) == 1
        for p in lineup.all_players
    )


def detect_premium_isolation_conflicts(lineup, df, primary_stack_team, secondary_stack_team=None):
    conflicts = []

    for p in lineup.all_players:
        if not p.is_hitter:
            continue

        salary_pct = player_salary_percentile(p, df)

        if (
            p.team != primary_stack_team
            and (secondary_stack_team is None or p.team != secondary_stack_team)
            and salary_pct >= 0.70
        ):
            conflicts.append(Conflict(
                code="OFFSTACK_PREMIUM_BAT",
                severity="soft",
                description=f"{p.player_name} is premium but off the primary/secondary stack structure",
            ))

        if (
            p.team != primary_stack_team
            and (secondary_stack_team is None or p.team != secondary_stack_team)
            and not has_adjacent_teammate(p, lineup)
        ):
            conflicts.append(Conflict(
                code="UNCORRELATED_PREMIUM",
                severity="medium",
                description=f"{p.player_name} has no correlated teammate in the lineup",
            ))

    return conflicts
```

Apply these only when the hitter is truly outside the primary/secondary stack story and not serving as a justified one-off runback within the active script.

Exclusion rule:
- do **not** fire `UNCORRELATED_PREMIUM` on a player who already triggers `PITCHER_SINGLE_RUNBACK` or `PITCHER_SINGLE_RUNBACK_MINOR` in the same lineup
- a justified one-off runback is already classified by the pitcher-conflict rules and should not be double-penalized as an uncorrelated premium

Conflict-object rule:
- player-specific conflicts should populate `player_id` so they can be deduplicated safely by `(player_id, code)`
- lineup-level conflicts such as `DOUBLE_PITCHER_SLUGFEST` should leave `player_id = None` and deduplicate by `(None, code)`

Conflict-composition rule:
- call both `detect_conflicts(...)` and `detect_premium_isolation_conflicts(...)` during lineup assembly
- merge their outputs into a single `list[Conflict]` on the `Lineup` object
- deduplicate by `(player_id, code)` when possible
- if two detectors produce the same code for the same player, keep only the higher-severity instance
- sort final merged conflicts by severity, then by code, so diagnostics and explanations remain deterministic

```python
def merge_conflicts(*conflict_lists: list[Conflict]) -> list[Conflict]:
    merged = {}
    severity_rank = {"soft": 1, "medium": 2, "hard": 3}

    for conflict_list in conflict_lists:
        for c in conflict_list:
            key = (c.player_id, c.code)
            if key not in merged or severity_rank[c.severity] > severity_rank[merged[key].severity]:
                merged[key] = c

    return sorted(
        merged.values(),
        key=lambda c: (-severity_rank[c.severity], c.code)
    )
```

Conflict thresholds such as `slate_ceiling_75th` and `slate_ceiling_40th` should be precomputed from the active slate before lineup generation. For hitter-specific conflict rules, prefer hitter-only ceiling thresholds unless a deliberate cross-position threshold is desired.

Define point-scale Stage 1 solver penalties separately from coherence-internal conflict weights:

```python
SOLVER_CONFLICT_PENALTIES = {
    "soft": 0.50,
    "medium": 1.50,
    "hard": 4.00,
}

conflict_penalties = sum(
    SOLVER_CONFLICT_PENALTIES[c.severity]
    for c in lineup.conflicts
)
```

These are raw-objective deductions used directly in the Stage 1 solver. They are distinct from any normalized or bounded conflict weighting used inside coherence scoring or Stage 2 candidate ranking.

---

## Coherence Engine

Uniqueness is not enough. The lineup must tell a plausible story.

A lineup is coherent when:
- captain choice matches script
- captain and stack structure align
- conflict profile is acceptable
- roster construction supports the stated scenario
- salary left over is strategic rather than accidental

Recommended derived metrics:
- `script_fit_score`
- `captain_alignment_score`
- `stack_integrity_score`
- `conflict_severity_score`
- `coherence_score`

### Coherence scoring rule
Coherence should be measurable, bounded, and interpretable. It should begin as a diagnostic score and a bounded ranking modifier. It should not overpower projection, ceiling, or core correlation logic.

Use named constants rather than hidden numbers so the behavior can be tuned safely.

Canonical helper functions used across captain classification, stack detection, salary-shape evaluation, premium-isolation conflict checks, and coherence scoring:

```python
def get_stack_teams(lineup):
    from collections import Counter

    hitter_teams = Counter(
        p.team for p in lineup.all_players if p.is_hitter
    )
    ranked = hitter_teams.most_common()
    primary = ranked[0][0]
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] >= 2 else None
    return primary, secondary


def most_common_team(players):
    from collections import Counter

    return Counter(p.team for p in players).most_common(1)[0][0]


def player_salary_percentile(player, df):
    return (df["salary"] <= player.salary).mean()


def player_ceiling_percentile(player, df):
    return (df["ceiling_score"] <= player.ceiling_score).mean()
```

Rules:
- `secondary_stack_team` is assigned only when that team has at least 2 hitters in the lineup
- a single bring-back hitter does **not** constitute a secondary stack
- `get_stack_teams(lineup)` is the canonical source for primary/secondary stack identity on showdown slates
- `most_common_team(players)` remains a valid convenience helper for code paths that already operate on a filtered list
- captain-related percentiles should be computed against the captain-eligible slate pool when used for captain classification or salary-shape logic

```python
PARTIAL_SCRIPT_FIT = 0.50
OFF_STACK_CAPTAIN_ALIGNMENT = 0.60
SOFT_CONFLICT_PENALTY = 0.05
MEDIUM_CONFLICT_PENALTY = 0.15
HARD_CONFLICT_PENALTY = 0.30

def compute_coherence(lineup, script_name: str, preset):
    hitter_pool = [p for p in lineup.all_players if p.is_hitter]
    primary_stack_team = most_common_team(hitter_pool)
    preferred = SCRIPT_PREFERRED_ARCHETYPES.get(script_name, [])

    script_fit = (
        1.0 if lineup.captain_archetype in preferred
        else PARTIAL_SCRIPT_FIT
    )

    captain_alignment = (
        1.0 if lineup.captain.team == primary_stack_team
        else OFF_STACK_CAPTAIN_ALIGNMENT
    )

    stack_players = [p for p in hitter_pool if p.team == primary_stack_team]
    orders = sorted(p.batting_order for p in stack_players if p.batting_order > 0)

    adjacent_pairs = sum(
        1 for i in range(len(orders) - 1)
        if orders[i + 1] - orders[i] == 1
    )
    max_pairs = max(len(orders) - 1, 1)
    stack_integrity = adjacent_pairs / max_pairs

    soft_count = sum(1 for c in lineup.conflicts if c.severity == "soft")
    medium_count = sum(1 for c in lineup.conflicts if c.severity == "medium")
    hard_count = sum(1 for c in lineup.conflicts if c.severity == "hard")

    conflict_severity = max(
        0.0,
        1.0 - (
            soft_count * SOFT_CONFLICT_PENALTY
            + medium_count * MEDIUM_CONFLICT_PENALTY
            + hard_count * HARD_CONFLICT_PENALTY
        ),
    )

    coherence_score = (
        0.25 * script_fit
        + 0.25 * captain_alignment
        + 0.25 * stack_integrity
        + 0.25 * conflict_severity
    )

    return coherence_score
```

### Coherence usage rule
- use coherence as a diagnostic score and tiebreaker first
- allow it to become a bounded lineup-score component only after normalization
- do not let coherence rescue a lineup that fails captain viability or hard-conflict rules
- partial credit is allowed, but hard conflicts should still dominate when present

The app should expose a lineup coherence score so users can tell the difference between “different” and “good different.”

---

## Uniqueness Engine

This is the most important replacement for ownership when ownership is absent.

### Uniqueness should come from:
1. **salary left over**
2. **captain diversity**
3. **lower-order or wraparound stack variants**
4. **portfolio diversity across scripts**
5. **reduced duplication of identical roster skeletons**
6. **contest-aware diversification**

### Salary-leftover bonus
Use as a mild bonus, not a main objective. In non-MME presets, it must be bounded by contest-aware salary discipline.

```python
salary_left = salary_cap - lineup_salary

if salary_left > max_salary_left_for_bonus:
    uniqueness_bonus = 0.00
elif salary_left >= 1000:
    uniqueness_bonus = 0.06 * salary_leftover_bonus_strength
elif salary_left >= 800:
    uniqueness_bonus = 0.05 * salary_leftover_bonus_strength
elif salary_left >= 600:
    uniqueness_bonus = 0.04 * salary_leftover_bonus_strength
elif salary_left >= 400:
    uniqueness_bonus = 0.03 * salary_leftover_bonus_strength
elif salary_left >= 200:
    uniqueness_bonus = 0.02 * salary_leftover_bonus_strength
else:
    uniqueness_bonus = 0.00
```

Default interpretation:
- single-entry and three-max should reward only modest salary left
- large-field MME can allow wider salary-left windows
- leaving major salary unused should never be the reason a lineup becomes optimal in tight contest modes

### Structural uniqueness rules
- prevent the optimizer from overusing the same captain in every lineup
- prevent every lineup from using the same 3-man core unless exposure settings explicitly allow it
- reward viable non-max-salary constructions
- support exposure caps at both captain and total-player level
- reward different roster skeletons, not just different single players

### Good uniqueness vs bad uniqueness

Good uniqueness:
- keeps captain viable
- keeps stack structure coherent
- improves differentiation without breaking the lineup story

Bad uniqueness:
- leaves salary for no reason
- forces low-order captains without real ceiling
- breaks script logic only to avoid duplication
- adds opposing pieces that destroy correlation

The app should never reward bad uniqueness enough to rescue an incoherent lineup.

---

## Captain Selection Engine

The app should rank captain candidates into interpretable buckets and archetypes.

### Tier 1 — Premium ceiling captains
- elite raw ceiling
- strong game environment
- usually top-order hitters or dominant pitchers in low-total scripts

### Tier 2 — Efficient tournament captains
- strong ceiling relative to salary
- strong team-total or platoon context
- often unlock better correlated utility builds

### Tier 3 — Structural contrarian captains
- not necessarily low-owned by projection, but lower-frequency in roster construction
- cheap enough to open premium combinations
- still need real ceiling pathways

### Captain archetypes
Tag each captain as one of:
- premium slugger
- top-order volume bat
- ace suppression captain
- efficient mid-tier bat
- wraparound salary-release captain
- script-dependent contrarian

Use an explicit classifier so the archetype system does not drift:

```python
def classify_captain_archetype(player, df, platform):
    salary_rank_pctile = player_salary_percentile(player, df)
    ceiling_rank_pctile = player_ceiling_percentile(player, df)

    if player.is_pitcher:
        return "ace suppression captain"

    if ceiling_rank_pctile >= 0.85 and salary_rank_pctile >= 0.80:
        return "premium slugger"

    if player.batting_order <= 2 and ceiling_rank_pctile >= 0.60:
        return "top-order volume bat"

    if player.batting_order >= 7 and salary_rank_pctile <= 0.30:
        return "wraparound salary-release captain"

    if ceiling_rank_pctile >= 0.50 and salary_rank_pctile <= 0.50:
        return "efficient mid-tier bat"

    return "script-dependent contrarian"
```

Rule:
- the classifier returns a **primary captain archetype**
- if a player plausibly fits multiple archetypes, classification priority follows the order of the function above
- for Version 1, script-fit scoring uses the primary archetype only; if a later version needs more nuance, add optional `secondary_captain_archetype` support rather than letting the classifier become ambiguous

### Captain scoring notes
- hitters should usually be preferred captains in tournament settings
- pitchers should gain relative captain weight only in low-total, suppression-friendly scripts
- the app should expose a hitter-captain preference slider rather than hard-ban pitcher captains

### Hitter-captain preference mechanic
`hitter_captain_preference` is a bounded captain-scoring bias applied to hitters relative to pitchers. Higher values favor hitter captains without banning pitcher captains.

Mechanically:

```python
# hitter_captain_preference is a multiplier applied to hitter captain scores
# relative to pitcher captain scores. Higher = stronger preference for hitter captains.
#
# Mechanically:
#   if is_hitter: captain_score *= hitter_captain_preference
#   if is_pitcher: captain_score *= (2.0 - hitter_captain_preference)

if is_hitter:
    captain_score *= hitter_captain_preference
else:
    captain_score *= (2.0 - hitter_captain_preference)
```

Default values by preset:
- `single_entry`: `1.15`
- `three_max`: `1.10`
- `large_mme`: `1.05`

Guardrails:
- keep `hitter_captain_preference` in a bounded range such as `1.0` to `1.3`
- treat this as a preset-level bias, not a hard rule
- allow pitcher captains to remain live in low-total scripts

---

## Stack Engine

### Baseline rules
- minimum stack size should be configurable
- 3-player hitter stack is the default baseline
- 4-player stacks are often the strongest DK showdown structure
- 5-player stacks should be allowed in extreme or concentrated scoring scripts
- the optimizer should not dismiss wraparound stacks automatically

### Recommended stack archetypes
- 4-2
- 5-1
- 3-3
- 4-1-1 if format/rules allow a looser interpretation
- double-pitcher variants in low-total scripts

### Captain-stack alignment
- hitter captain should usually pair with same-team hitters
- pitcher captain should usually pair with that pitcher’s offense, not the opposing offense
- value captain should be judged by what premium combinations it unlocks

---

## Scenario Engine

The app should not produce one monolithic lineup set. It should produce lineups across plausible game scripts.

### Recommended scripts
1. **Pitcher duel**
   - lowers hitter cluster aggression
   - increases pitcher captain viability
   - raises double-pitcher viability

2. **Favorite controls game**
   - boosts favorite pitcher
   - boosts favorite top-order stack
   - supports 4-2 and 5-1 favorite builds

3. **Slugfest**
   - upgrades hitter ceilings
   - downgrades double-pitcher builds
   - rewards larger stacks and premium bats

4. **Underdog surprise**
   - boosts underdog stack viability
   - downgrades favorite pitcher
   - useful for tournament diversification

5. **Wraparound value script**
   - modest upgrade to lower-order values on strong team totals
   - encourages cheaper correlated lineup shapes

### Default script modifiers
All script modifiers must declare units clearly.
- `*_mult` values are score multipliers
- `*_bonus` values applied to lineups must be additive point-scale bonuses after normalization or documented point-scale transforms
- team stack bonuses apply at the stack/team level, not once per hitter, unless explicitly noted

```python
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
```

Modifier unit rules:
- `*_mult` values are direct multipliers on player-level scores
- `*_bonus` values are applied as a percentage of a qualifying stack's or structure's average median score, converted into point-scale, and then added once per qualifying structure

Example:

```python
# direct multiplier
pitcher_ceiling_score *= script_modifier["pitcher_ceiling_mult"]

# point-scale additive stack bonus, applied once per qualifying stack
stack_script_bonus = avg_stack_median * script_modifier["fav_stack_bonus"]
```

`fav_stack_bonus` and `dog_stack_bonus` should be applied at the qualifying stack level, not once per hitter, to avoid double-counting.

`bottom_order_bonus` should apply only to hitters in lineup spots 6-9 and only when they are part of an approved wraparound or correlated value structure.

Define wraparound eligibility explicitly:
- an approved wraparound structure exists when at least 2 hitters from batting-order positions 7-9 on the same team are selected
- that team must have `implied_team_score` above the slate median
- a single bottom-order hitter without a same-team partner in the 6-9 range does not qualify
- a 6-7 or 6-7-8 correlated value structure may also qualify when the active script is `wraparound` and the team-total condition is met

Helper:

```python
def qualifies_for_wraparound_bonus(lineup, team, slate_median_team_total):
    hitters = [
        p for p in lineup.all_players
        if p.is_hitter and p.team == team and 6 <= p.batting_order <= 9
    ]
    orders = sorted({p.batting_order for p in hitters})
    team_total = max((p.implied_team_score for p in lineup.all_players if p.team == team), default=0.0)

    has_two_from_79 = sum(1 for o in orders if 7 <= o <= 9) >= 2
    has_correlated_value_chain = orders[:2] == [6, 7] or orders[:3] == [6, 7, 8]

    return team_total > slate_median_team_total and (has_two_from_79 or has_correlated_value_chain)
```

### Preferred captain archetypes by script
Use a controlled captain-archetype vocabulary in one place so the strings cannot drift.

```python
SCRIPT_PREFERRED_ARCHETYPES = {
    "pitcher_duel": ["ace suppression captain"],
    "fav_controls": ["premium slugger", "top-order volume bat"],
    "slugfest": ["premium slugger", "top-order volume bat"],
    "underdog": ["efficient mid-tier bat", "top-order volume bat"],
    "wraparound": ["wraparound salary-release captain", "efficient mid-tier bat"],
}
```

Partial fit is allowed. Secondary archetype matches should receive reduced, but non-zero, script-fit credit.

### Probabilistic script weighting
The app should assign default script weights from betting context rather than treat every script as equally likely.

These weights do not need to be perfect. They do need to be explicit and inspectable.

```python
def default_script_weights(over_under, abs_spread):
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
```

Each script should modify:
- captain candidate pool
- hitter ceiling multipliers
- pitcher viability
- stack preference
- uniqueness preference

Generate candidate lineups by script, then combine and deduplicate.

---

## Final Lineup Objective

The blueprint uses **two related but distinct lineup objective systems**.

### Stage 1 — Raw solver objective
The lineup solver should optimize the raw expanded showdown objective directly. This is the objective used when building a single lineup candidate inside a script bucket.

Solver-context rule:
- the Stage 1 solver is **always** invoked inside a specific `script_name` bucket
- `script_name` is known before solve time and passed into Stage 1 scoring/objective construction
- `coherence_bonus` is therefore computed against the active script, not against a generic slate-wide context
- a lineup generated under `pitcher_duel` is scored for coherence against `pitcher_duel` captain archetypes and script preferences

```python
lineup_score = (
    projection_component
    + ceiling_component
    + correlation_component
    + stack_component
    + captain_stack_component
    - conflict_component
)
```

Expanded form:

```python
lineup_score = (
    sum(selected_player_median_scores)
    + sum(selected_player_ceiling_boosts)
    + sum(pairwise_correlation_bonuses)
    + stack_bonus
    + captain_stack_bonus
    - conflict_penalties
)
```

Where:
- `selected_player_median_scores` is the baseline projection layer
- `selected_player_ceiling_boosts` is the **incremental** upside layer, defined as `ceiling_score - median_score`
- in the solver, both terms are applied role-dependently: utility uses the base score and captain uses `multiplier_points_factor * base_score`
- these layers are additive and non-overlapping
- `pairwise_correlation_bonuses` captures local pair relationships, including captain-amplified pair bonuses where applicable
- `stack_bonus` is a one-time lineup-level reward for achieving a valid primary stack shape
- `captain_stack_bonus` is a one-time lineup-level reward when the captain is integrated into the primary stack story
- coherence and uniqueness are **Stage 2 portfolio-ranking terms**, not Stage 1 raw-solver terms
- pairwise, stack, captain-stack, and conflict terms must each be defined in point-scale units before entering the Stage 1 raw objective

Use these default lineup-level stack formulas unless a later calibration pass overrides them:

```python
stack_bonus = avg_median_of_stack_players * 0.10 if stack_size >= min_stack_size else 0.0
captain_stack_bonus = avg_median_of_stack_players * 0.08 if captain_on_primary_stack_team else 0.0
```

Captain-stack rule:
- for hitter captains, `captain_stack_bonus` applies when the captain is a hitter on the primary stack team
- for pitcher captains, only apply an equivalent captain-stack bonus when the pitcher is paired with the correct same-team offensive script and the lineup story remains coherent

If ownership is present, add an optional ownership leverage component. If ownership is absent, the app should not expose ownership-based formulas in the UI.

### Stage 2 — Normalized portfolio objective
After candidate lineups are generated by the raw solver, compare and rank them across scripts using the normalized weighted `final_optimizer_score` defined in the scoring architecture section.

Stage 2 normalization groups Stage 1 raw terms into candidate-pool components as follows:
- `norm_projection`: derived from `sum(median_scores)` across the candidate pool
- `norm_ceiling`: derived from `sum(ceiling_boosts)` across the candidate pool
- `norm_correlation`: derived from `sum(pairwise_bonuses) + stack_bonus + captain_stack_bonus` across the candidate pool
- `norm_uniqueness`: computed fresh at Stage 2 from salary-leftover and structural uniqueness
- `norm_coherence`: computed fresh at Stage 2 from `compute_coherence(...)` against the lineup's `script_name`
- `norm_conflict`: derived from `conflict_penalties` across the candidate pool

Normalization method:
- use rank-percentile normalization across the candidate pool for each Stage 2 component
- this remains consistent with the showdown small-slate normalization strategy used elsewhere in the document

Use this Stage 2 score for:
- greedy portfolio assembly
- review-pool ordering
- cross-script comparison
- candidate tie-breaking when raw lineup scores are close but structure differs materially

Use the raw Stage 1 solver score for:
- candidate generation
- script-local lineup optimization
- feasibility-preserving objective optimization in PuLP / CP-SAT

---

## Solver Design

Start with a binary optimization model.

### Recommended implementation path
- **Version 1A**: PuLP with linearized pairwise terms
- **Version 1B**: OR-Tools CP-SAT if you want more natural handling of pairwise interactions, conflict rules, and portfolio logic
- **Version 2**: richer portfolio selection and scenario-aware candidate generation

### Solver context
Define a solver-context contract so Stage 1 inputs are explicit rather than inferred:

```python
@dataclass(frozen=True)
class SolverContext:
    script_name: str
    preset: str
    platform: str
    stage2_ranking_weights: dict[str, float]
    script_modifier: dict[str, float]
    ownership_available: bool
```

Docstring rule:
- `stage2_ranking_weights` is carried for post-solve candidate scoring and portfolio comparison
- it is **not** used directly in the Stage 1 PuLP / CP-SAT raw objective function
- the Stage 1 solver objective remains the raw expanded lineup score defined above

### Variables

For each player `i`:
- `c_i = 1` if selected as Captain
- `u_i = 1` if selected as Utility
- `s_i = c_i + u_i`

For pairwise correlations:
- `p_ij = 1` if both players `i` and `j` are selected

### Role-dependent captain contribution rule

Player-level projection and upside terms are **slot-dependent** inside the solver objective. The captain slot contributes the platform point multiplier to both the baseline median layer and the incremental ceiling layer.

For each player `i` in the PuLP / CP-SAT objective:

```python
objective += median_score_i * u_i + (median_score_i * multiplier_points_factor) * c_i
objective += ceiling_boost_i * u_i + (ceiling_boost_i * multiplier_points_factor) * c_i
```

Rules:
- do **not** pre-multiply stored player scores globally
- the solver must evaluate each player in both utility and captain roles simultaneously
- this remains linear in `u_i` and `c_i` and is the correct way to preserve captain-score integrity in the raw objective
- captain salary already follows the same role-dependent pattern in the salary constraint

### Pairwise linearization note

PuLP cannot use raw quadratic products like `s_i * s_j` directly in a linear objective. If using PuLP, linearize pairwise terms with standard constraints:

```python
p_ij <= s_i
p_ij <= s_j
p_ij >= s_i + s_j - 1
```

Then use `p_ij` in the objective for pairwise bonuses and penalties.

If the app moves directly to CP-SAT, pairwise and indicator logic becomes cleaner, especially for showdown correlation and portfolio-stage logic.

### Captain-correlation amplification implementation note

Captain-correlation amplification in the Stage 1 PuLP solver requires additional auxiliary variables beyond `p_ij` if you want exact captain-pair bonuses.

For **V1A (PuLP)**, two acceptable approaches exist:

**Approach A — full linearization**
For each pair `(i, j)`, create `cp_ij = 1` if both are selected **and** `i` is captain.
Linearize with:

```python
cp_ij <= c_i
cp_ij <= s_j
cp_ij >= c_i + s_j - 1
```

Then the objective can add:

```python
base_bonus * p_ij + (multiplier_points_factor - 1) * base_bonus * (cp_ij + cp_ji)
```

This is exact but increases auxiliary variable count substantially.

**Approach B — deferred amplification**
Apply base pairwise bonuses only in the Stage 1 PuLP objective. Then apply captain amplification later when computing Stage 2 `norm_correlation` after candidate lineups are generated. This is simpler and loses only marginal Stage 1 accuracy.

Recommended default:
- **V1A / PuLP**: use Approach B unless exact captain-pair modeling is worth the added complexity
- **V1B / CP-SAT**: exact captain amplification can be modeled directly and is the preferred long-term implementation path

### Core constraints

```python
sum(c_i) == 1
sum(u_i) == 5
c_i + u_i <= 1 for all i
sum(captain_salary_i * c_i + salary_i * u_i) <= salary_cap
```

### Team presence

```python
for each team:
    sum(s_i for players on that team) >= 1
```

### Stack constraint

```python
max_team_hitters_selected >= min_stack_size
```

### Uniqueness constraint across generated lineups

For each prior lineup `k`:

```python
sum(s_i for i in players_in_prior_lineup_k) <= 6 - uniqueness_min
```

For DraftKings 6-man lineups and `uniqueness_min = 2`, that becomes:

```python
sum(shared_selected_players) <= 4
```

### Optional strategy constraints
- max opposing hitters against selected pitcher
- allow/disallow double-pitcher
- max bottom-order hitters
- captain exposure cap
- player exposure cap
- team exposure cap
- minimum salary spend by contest preset
- maximum salary left for uniqueness bonus by contest preset

---

## Portfolio Construction Engine

Portfolio construction should be explicit. It is not enough to say “generate lineup #2 with a uniqueness rule.”

### Stage 1 — candidate generation
Generate candidate lineups by script and captain archetype:
- pitcher duel candidates
- favorite-controls-game candidates
- slugfest candidates
- underdog surprise candidates
- wraparound value candidates

Each candidate should carry:
- quality score
- script tag
- captain tag
- stack tag
- conflict profile
- salary left
- coherence score

### Stage 2 — portfolio assembly
Start with a buildable algorithm.

#### Recommended default algorithm: greedy sequential selection with diminishing returns
1. sort candidate pool by quality threshold
2. select the best first lineup
3. for each subsequent selection, rescore every remaining candidate against the existing portfolio
4. apply penalties for:
   - repeated captain
   - repeated 3-man or 4-man core
   - repeated stack skeleton
   - repeated script
   - repeated salary shape
5. select the candidate with the best adjusted portfolio contribution
6. repeat until the desired lineup count is reached

### Example portfolio contribution score

```python
DEFAULT_PORTFOLIO_OVERLAP_PENALTIES = {
    "captain_overlap_penalty": 3.00,
    "core_3man_overlap_penalty": 2.00,
    "core_4man_overlap_penalty": 3.50,
    "stack_overlap_penalty": 1.50,
    "script_overlap_penalty": 1.00,
    "salary_shape_overlap_penalty": 0.50,
}

portfolio_contribution = (
    candidate_quality
    - captain_overlap_penalty
    - core_overlap_penalty
    - stack_overlap_penalty
    - script_overlap_penalty
    - salary_shape_overlap_penalty
)

Preset rule:
- use `DEFAULT_PORTFOLIO_OVERLAP_PENALTIES` unless a preset explicitly overrides one or more fields
- small-field review pools may use stronger captain/script overlap penalties than large-field MME
```

These values should usually be stronger for small-field review pools than for large-field MME.

`salary_shape_overlap_penalty` should be computed from a defined structural bucket, such as:
- same salary-left bucket
- same stars-and-scrubs shape
- same cheap-captain salary-release build

Define salary-shape buckets explicitly so overlap can be computed deterministically:

```python
salary_left_buckets = [
    (0, 500, "tight"),
    (501, 1500, "modest"),
    (1501, 3000, "loose"),
    (3001, 50000, "extreme"),
]


def get_salary_shape(lineup, captain_pool_df, slate_min_salary):
    captain = lineup.captain
    captain_salary_pctile = player_salary_percentile(captain, captain_pool_df)
    min_utility_salary = min(u.salary for u in lineup.utility)

    if captain_salary_pctile <= 0.30:
        build = "cheap_captain_release"
    elif min_utility_salary <= slate_min_salary * 1.1:
        build = "stars_and_scrubs"
    else:
        build = "balanced"

    bucket = next(
        label for lo, hi, label in salary_left_buckets
        if lo <= lineup.salary_left <= hi
    )
    return f"{build}_{bucket}"
```

Rule:
- two lineups share a salary shape when `get_salary_shape(...)` returns the same string
- salary shape is used for **portfolio overlap comparison only**, not as a standalone Stage 1 scoring feature
- `captain_salary_pctile` should be measured against the captain-eligible slate pool, not the full player table

### Upgrade path
A secondary ILP or CP-SAT portfolio selector can be added later:
- maximize total portfolio quality
- subject to exposure caps
- subject to minimum quality threshold per lineup
- subject to captain / stack / script diversity goals

Start with the greedy diminishing-returns approach because it is far easier to implement and inspect.

### Single-entry review-pool diversification

Single-entry requires one more layer: the app should separate the **best final recommendation** from the **top-N review set** shown for manual inspection.

Rule:
- the final single-entry lineup remains the highest raw single-lineup score after all single-entry preset guards are applied
- the single-entry top-review set is then assembled greedily from the candidate pool using mild repetition penalties

Default review-pool adjustment:

```python
review_adjusted_score = (
    raw_lineup_score
    - review_same_captain_penalty * prior_review_count_for_same_captain
    - review_same_script_penalty * prior_review_count_for_same_script
)
```

This solves the exact failure mode from the second dry run:
- the top single-entry lineup can still be Ohtani if it is truly best
- the review set no longer collapses into ten near-duplicate Ohtani-captain builds when deGrom or Sasaki also remain viable

Use this review-pool logic only for the displayed candidate set, not for the single final recommendation.

---

## Contest Presets

Contest type should shape uniqueness, captain spread, and salary-leftover posture.

### Default starter presets

```python
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
```

These are **editable starter presets**, not final doctrine. They should preload sensible defaults and remain user-adjustable.

Platform-relative salary-band rule:
- `expected_salary_band_min` and `expected_salary_band_max` are calibration ranges, not hard salary-cap legality rules
- for FanDuel, scale DraftKings-first salary-band defaults proportionally by platform cap, or define explicit FD overrides

Script-weight override rule:
- `script_weights_override` fully replaces the base script weights when present
- it is **not** a partial patch; all five script keys must be present in the override dict
- base weights from `default_script_weights(...)` are used only when `script_weights_override is None`
- validate that effective weights sum to `1.0`; renormalize or reject invalid overrides

```python
def get_effective_script_weights(slate_ou, slate_spread, preset):
    if preset.get("script_weights_override"):
        return preset["script_weights_override"]
    return default_script_weights(slate_ou, abs(slate_spread))
```
- V1 can remain DK-first because DK Showdown is the primary target, but FD handling should not silently reuse DK bands without scaling

Example helper:

```python
def compute_platform_relative_salary_band(dk_band_min, dk_band_max, cap, dk_cap=50000):
    scale = cap / dk_cap
    return int(dk_band_min * scale), int(dk_band_max * scale)
```

Implementation rule:
- treat `PRESETS` as the canonical source of truth for preset defaults rather than scattering these values across prose sections
- if a preset omits a field, merge in the shared default from the relevant constant block before use

Preset overrides should merge cleanly with the slate's default script weights rather than replacing the scenario engine entirely. Contest type should meaningfully change the portfolio's script mix: single-entry should lean toward more stable favorite-control structures, while large-field MME should widen underdog and wraparound exposure.

Additional preset rules:
- `allow_double_pitcher` controls eligibility only; game-total and script rules still govern whether double-pitcher is strategically viable on a given slate
- `leverage_weight` is active only when ownership data exists; when ownership is absent, ignore it rather than faking leverage from missing ownership
- `leverage_weight = 0.50` is a moderate starter default; values closer to `0.75` to `1.00` increase contrarian pressure only on slates with ownership data

Preset doctrine from the 3.2.1 dry run:
- single-entry and three-max should not surface extremely low-salary, cheap-captain builds as default best lineups
- structural uniqueness should be dampened outside MME
- tighter contest modes should require stronger captain viability and stronger salary discipline
- a lineup using only $43,500 of $50,000 is acceptable only as a user-forced experimental build, not as the default top result in a tight contest preset

Additional single-entry doctrine from the second dry run:
- keep the **final** single-entry recommendation fully raw-score driven; do not lower lineup quality just to force faux diversity
- diversify the **review pool**, not the final #1 lineup
- if the viable single-entry captain pool contains at least three captains, the default top-10 review set should contain at least two distinct captains
- cap any one captain at `review_max_same_captain` appearances in the default single-entry top-review set
- apply `review_same_captain_penalty` and `review_same_script_penalty` only when building the review set shown to the user, not when selecting the highest-scoring final recommendation

---
## Dry-Run Lessons Locked In

The 3.2.1 test on the uploaded showdown CSV established several non-negotiable implementation lessons:

1. **Captain multiplier integrity mattered, but was not the problem.** The dry run confirmed that the 1.5x Captain multiplier was applied correctly to both salary and scoring. The bad outcome came from preset tuning, not from a multiplier bug.
2. **No-ownership showdown slates can become too clever by default.** Without ownership, cheap captains and salary-left bonuses can overtake raw lineup quality unless tighter presets explicitly restrain them.
3. **Single-entry and small-field modes need harder discipline than MME.** That means stronger captain pools, stronger minimum salary spend, smaller wraparound exposure, and weaker uniqueness pressure.
4. **Structural uniqueness is still useful, but only after lineup quality is protected.** It is a tiebreaker, not the main engine, in tighter contest modes.
5. **The blueprint should encode these lessons as defaults, not leave them to interpretation.**
6. **The second dry run fixed spend discipline but over-concentrated the single-entry review set.** A healthy single-entry preset can still produce one clear best lineup while showing a broader review set for human inspection.
7. **Captain diversity belongs in the review layer, not the final recommendation layer.** That is the narrowest way to improve single-entry usability without weakening lineup quality.
8. **The top single-entry review set should remain disciplined even when diversity is added.** Review-pool diversification must not reintroduce low-spend or fake-contrarian captain behavior.

---

## Guardrails and Regression Test Pack

The optimizer is now mature enough that lessons learned should be locked into regression tests, not preserved only in prose.

### Core regression pack
The codebase should include a deterministic test suite that runs on saved showdown fixtures and verifies:

- Captain multiplier salary logic
- Captain multiplier projection and ceiling logic
- lineup legality by platform
- minimum salary spend behavior by contest preset
- captain-pool narrowing by contest preset
- review-pool captain diversity behavior
- export schema validity
- solver infeasibility diagnostics
- ownership-optional behavior when ownership is blank
- stale-lineup and incomplete-slate warning behavior

### Required regression fixtures
At minimum, maintain fixture slates for:
- a standard one-game showdown slate with full batting orders
- a low-total pitcher-friendly showdown slate
- a high-total slugfest-style showdown slate
- a slate with missing batting-order values
- a slate with blank ownership
- a slate with switch-hitters present
- a slate that intentionally becomes infeasible under aggressive locks/excludes

### Regression assertions from real dry runs
These should be explicit pass/fail checks:
- Captain salary and Captain scoring must both reflect the platform multiplier
- default single-entry should not produce absurd under-spend as the top recommendation
- review-pool diversity should improve variety without changing the raw best lineup unless a true scoring tie or near-tie exists
- single-entry review pools should not collapse to one captain when multiple viable captains exist
- MME presets may accept wider salary-left behavior than single-entry presets

### Guardrail doctrine
Guardrails should mostly be implemented as:
- default preset rules
- validation warnings
- regression tests
- review-layer diversification

Guardrails should **not** quietly distort the final highest-scoring lineup outside the selected preset logic.


## Calibration Layer

The optimizer needs an explicit calibration layer between core scoring and diagnostics. Calibration is where the app learns how aggressively to behave on a given slate without changing the underlying scoring math.

### Goals of calibration
- distinguish a genuinely concentrated slate from optimizer over-concentration
- distinguish healthy uniqueness from fake-contrarian drift
- keep single-entry and three-max behavior disciplined without flattening MME upside
- make salary-spend behavior interpretable by preset rather than judged by one hard floor alone
- prevent double-counting when several modules all reward the same team, captain, or script path

### Slate concentration detector
Add an internal slate-concentration score built from:
- gap between the top raw Captain score and the 2nd/3rd Captain scores
- gap between team implied totals
- number of viable Captains above the current preset floor
- spread of top stack scores
- over/under environment

Example implementation:

```python
TARGET_VIABLE_CAPTAINS = 8

def compute_slate_concentration(df, preset):
    captain_pool = get_captain_pool(df, preset)
    ceilings = sorted(captain_pool.ceiling_score.values, reverse=True)

    captain_gap = (
        (ceilings[0] - ceilings[2]) / max(ceilings[0], 1e-9)
        if len(ceilings) >= 3 else 1.0
    )

    team_totals = df.groupby("team")["implied_team_score"].first()
    total_gap = abs(team_totals.max() - team_totals.min()) / max(team_totals.mean(), 1e-9)

    viable_count = len(captain_pool)
    viable_score = max(0.0, 1.0 - (viable_count / TARGET_VIABLE_CAPTAINS))

    concentration = 0.40 * captain_gap + 0.30 * viable_score + 0.30 * total_gap

    if concentration >= 0.55:
        label = "high"
    elif concentration >= 0.30:
        label = "medium"
    else:
        label = "low"

    return {"score": round(concentration, 3), "label": label}
```

Interpretation:
- **High concentration**: one or two Captains clearly separate; tighter review pools are acceptable
- **Medium concentration**: top Captain tier is narrow but not singular; mild review-pool diversification is preferred
- **Low concentration**: several Captains and stack scripts are tightly clustered; wider review-pool diversity is expected

This detector should influence:
- review-pool diversity targets
- captain-distribution warnings
- script-mix expectations
- diagnostics language such as “this slate is naturally narrow”

### Team-total calibration on showdown slates
On a two-team showdown slate, normalized `implied_team_score` behaves more like a side-of-game toggle than a granular player feature. That is acceptable, but the app should calibrate how heavily it matters.

Use damped team-total weighting when the team-total gap is small:

```python
def get_team_total_coefficient(team_a_implied, team_b_implied):
    team_total_gap = abs(team_a_implied - team_b_implied)

    if team_total_gap < 0.5:
        return 0.03
    elif team_total_gap < 1.0:
        return 0.045
    return 0.06
```

### Preset salary-spend bands
Use salary discipline as a calibrated range, not only a hard floor.

Recommended default bands for DK Showdown:
- **single_entry**: expected spend band `$48,500–$50,000`
- **three_max**: expected spend band `$48,000–$50,000`
- **large_mme**: no narrow expected band; use informational monitoring only

These are not hard bans in every context. They should drive:
- warnings
- diagnostics
- regression checks
- “outside expected range” notes in review output

### Captain review-pool calibration
The final best lineup remains score-first. Calibration belongs in the review layer.

For small-field presets:
- if `slate_concentration_score` is high, allow a narrower review pool
- if `slate_concentration_score` is medium or low, require broader Captain representation in the review set
- do not force review-pool diversity that admits Captains below the preset viability floor

### Calibration rule
Prefer calibration changes that alter:
- preset behavior
- review-pool diversity
- diagnostics thresholds
- warning bands

Avoid using calibration to quietly rewrite the underlying player-scoring model unless repeated cross-slate evidence demands it.


## Diagnostics and Failure Analysis

The app should not only return lineups. It should also explain what happened when the optimizer behaved in a surprising way.

### Required diagnostics views
- **Why this lineup won** — ranked reasons for the selected lineup
- **Why this lineup lost** — ranked penalties versus the current top lineup
- **Why this slate is narrow** — captain concentration, team-total gap, and script concentration summary
- **Why generation failed** — infeasibility or empty-candidate diagnostics
- **Why confidence is reduced** — missing order, uncertain starter inference, stale data, or incomplete ownership notice

### Ranked reason format
Each lineup comparison should be able to emit the top few positive and negative drivers in order, for example:
1. stronger captain ceiling
2. better same-team adjacency cluster
3. better script alignment
4. less salary wasted beyond preset preference
5. fewer pitcher-vs-opposing-stack conflicts

### Surprise-result diagnostics
If a lineup lands outside expected behavior bands, the app should surface a structured explanation, for example:
- unusually low salary spend justified by strong Captain-plus-core fit
- narrow captain pool due to captain-relative-floor filtering
- wide MME script spread due to preset override
- reduced stack size caused by salary, locks, or conflict penalties

The goal is to make strange-looking results inspectable, not mysterious.

## Cross-Slate Robustness

The optimizer should be tested and tuned against more than one saved slate. A blueprint that only works on one showdown pool is not yet robust.

### Cross-slate validation doctrine
Whenever a scoring or preset change is made, run a compact validation pack on multiple fixture slates and compare:
- top lineup salary spend
- top lineup captain
- captain-pool size by preset
- review-pool captain distribution
- scenario distribution
- average salary used by preset
- conflict frequency
- infeasibility rate

### Slate-behavior bands
The app should learn to distinguish normal slate concentration from optimizer overreach.

Examples:
- on a highly concentrated slate, a single dominant captain may legitimately appear often
- on an open slate, review-pool diversity should widen naturally
- on low-total slates, double-pitcher viability should rise
- on high-total slates, hitter captain concentration should generally increase

### Cross-slate robustness score
Add an internal validation summary that reports whether a new scoring or preset change:
- improved behavior across slates
- improved one slate but hurt another
- introduced new under-spend or over-concentration issues
- changed captain pools more than expected

This can be a developer-facing report rather than a user-facing screen.

### Rule
Do not promote a tuning change into default preset doctrine unless it survives validation on more than one representative showdown slate.

## Streamlit App Structure

The app should feel like both an optimizer and a strategy workstation.

### Sidebar
- platform
- number of lineups
- salary cap
- uniqueness minimum
- min stack size
- allow double-pitcher
- hitter captain preference
- scenario weights
- contest preset
- optional ownership toggle if data exists

### Main tabs

#### Tab 1 — Slate Dashboard
- matchup header
- spread, total, implied scores
- favorite/underdog summary
- platoon summary
- hot/cold flags
- generated script summary in plain English
- slate confidence warning banner if needed

#### Tab 2 — Player Pool
- sortable table
- median score
- ceiling score
- captain score
- value
- order
- platoon tag
- form tag
- lock / exclude / captain-only / utility-only controls

#### Tab 3 — Captain Engine
- captain rankings
- captain tiers
- captain scatterplot
- show salary-adjusted captain efficiency
- show captain-by-script rankings

#### Tab 4 — Stack + Script Builder
- top stack candidates
- adjacency clusters
- wraparound suggestions
- script-based stack recommendations
- team-level comparison cards

#### Tab 5 — Portfolio Controls
- exposure caps
- captain exposure caps
- team exposure caps
- salary-leftover preference
- uniqueness aggressiveness
- optional ownership settings if populated

#### Tab 6 — Lineups + Review
- generated lineups
- stack tags
- scenario tags
- salary used / left
- explanation block
- exposure dashboard
- similarity heatmap
- export button

#### Streamlit execution notes
Streamlit re-runs the full script on every widget interaction. Scoring, candidate generation, and portfolio assembly can become expensive.

Implementation rule:
- use `st.cache_data` for pure data transforms and deterministic feature generation
- use `st.session_state` for mutable optimizer state such as locks, excludes, candidate lineups, selected portfolio, and solver diagnostics
- only recompute scores and candidate lineups when the CSV, platform, or scoring settings change

---

## Lineup Output Requirements

Each lineup should display:

- Captain
- Utility players
- total salary used
- salary remaining
- median projection
- ceiling projection
- correlation score
- uniqueness score
- coherence score
- final optimizer score
- stack description
- scenario tag
- conflict flags

### Example explanation block
- “4-man TEX stack with 2-3-4 adjacency”
- “Captain rated highly as top-order HR ceiling bat”
- “Leaves $700 salary to reduce duplication risk”
- “Pitcher conflict limited to one premium run-back”
- “Fits favorite-controls-game script”

### Explanation template example

```python
def build_explanation(lineup):
    lines = []
    lines.append(lineup.stack_description)
    lines.append(f"Captain: {lineup.captain.player_name} — {lineup.captain_archetype}")
    if lineup.salary_left >= 200:
        lines.append(f"Leaves ${lineup.salary_left} to reduce duplication")
    for flag in lineup.conflict_flags:
        lines.append(f"Conflict: {flag}")
    lines.append(f"Script: {lineup.scenario_tag}")
    return lines
```

Explanations should be assembled from detected reasons and lineup metadata, not free-written from scratch every time. That keeps the output consistent, auditable, and tied to the actual optimizer logic.

This explanation layer is not optional. It is what makes the tool usable.

---

## Exposure Dashboard

For multi-lineup sets, show:

- captain exposure by player
- total player exposure
- team exposure
- stack frequency by team
- average salary used
- average salary left over
- pitcher frequency
- scenario distribution
- lineup similarity heatmap

The goal is not just to generate lineups. The goal is to make the portfolio inspectable.

---

## Export Requirements

“Export to DK CSV” or “Export to FD CSV” is not complete unless the blueprint defines the output format.

### Export rules
- export format is platform-specific and must be defined in `platform_rules.py`
- export headers must be pinned in `PlatformConfig`
- export validation must run before download
- export must use the exact identifier and column order expected by the target site

### Minimum export specification
For each platform, define:
- required headers
- whether export uses player IDs, player names, or site-specific roster strings
- Captain / MVP column naming
- FLEX / UTIL column naming
- example exported row

### Rule
Do not leave export schema to the final step of implementation. It must be specified early.

---

## Validation Rules

The app must validate:

- required columns exist
- salary is numeric
- projection columns are numeric
- batting order is reasonable for hitters
- at most one starting pitcher per team is inferred for platoon logic
- no lineup violates salary or roster rules
- no lineup violates minimum salary spend for the selected contest preset
- no lineup violates uniqueness rules
- captain and utility duplication is impossible
- Captain multiplier salary and scoring are applied consistently in optimization and display
- ownership-specific controls are hidden when ownership is blank
- export schema is valid before download
- regression-test expectations pass for the selected preset and slate fixture
- cross-slate validation warnings can be generated for developer-mode comparisons

### Additional validation behavior
- stale-lineup detection if batting orders or starters appear out of sync with uploaded data
- scratch-risk warnings from `injury_status`
- slate confidence score based on completeness of orders and starter inference
- solver infeasibility diagnostics if locks, excludes, stack rules, salary rules, or team rules eliminate all valid lineups

### Solver infeasibility handling
If the solver returns infeasible:
- display a diagnostic rather than an empty lineup set
- identify likely causes such as salary cap, roster count, team presence, stack minimum, or excessive locks/excludes
- suggest the user relax the offending constraints
- preserve current settings in session state so the user can adjust and retry quickly

### Warning examples
- “Ownership data not detected; ownership-based leverage controls disabled.”
- “Opposing pitcher hand could not be inferred for this game; platoon bonuses reduced.”
- “Three hitters have missing batting order; order adjustments defaulted conservatively.”
- “Default single-entry preset rejected a lineup below the minimum salary spend threshold.”
- “Captain multiplier audit passed: salary and scoring both reflect the platform multiplier.”
- “Slate confidence reduced: lineup and starter data appear incomplete.”

The app should warn clearly rather than fail silently.

---

## Data Model

```python
@dataclass
class Player:
    player_id: str
    player_name: str
    team: str
    opp: str
    position: str
    salary: int
    batting_order: int
    hand: str
    opp_pitcher_hand: str | None
    is_pitcher: bool
    is_hitter: bool
    is_starting_pitcher: bool
    spread: float
    over_under: float
    implied_team_score: float
    l5_fppg: float
    l10_fppg: float
    szn_fppg: float
    ppg_projection: float
    value_projection: float
    ownership_projection: float | None
    form_signal: float
    platoon_edge: str
    median_score: float
    ceiling_score: float
    captain_score: float

@dataclass
class Conflict:
    code: str
    severity: str  # "soft", "medium", "hard"
    description: str
    player_id: str | None = None

@dataclass
class Lineup:
    captain: Player
    utility: list[Player]
    all_players: list[Player]
    total_salary: int
    salary_left: int
    median_projection: float
    ceiling_projection: float
    correlation_score: float
    uniqueness_score: float
    coherence_score: float
    final_score: float
    stack_description: str
    scenario_tag: str
    captain_archetype: str
    explanation: list[str]
    conflict_flags: list[str]
    conflicts: list[Conflict]

@dataclass
class SlateSettings:
    platform: str
    salary_cap: int
    num_lineups: int
    min_stack_size: int
    uniqueness_min: int
    allow_double_pitcher: bool
    hitter_captain_preference: float
    captain_viability_pctile: int
    captain_relative_floor: float
    min_salary_spend: int
    max_salary_left_for_bonus: int
    max_captain_exposure: float
    top_review_count: int
    review_min_distinct_captains: int
    review_max_same_captain: int
    review_same_captain_penalty: float
    review_same_script_penalty: float
    expected_salary_band_min: int
    expected_salary_band_max: int
    slate_concentration_score: float | None
    salary_leftover_bonus_strength: float
    max_opposing_hitters_vs_pitcher: int
    contest_preset: str
    script_weights: dict[str, float]
    script_weights_override: dict[str, float] | None
    portfolio_overlap_penalties: dict[str, float]
    lineup_score_weights: dict[str, float]
    use_ownership: bool
    leverage_weight: float
    developer_mode: bool
    run_regression_checks: bool
    enable_cross_slate_validation: bool
```

---

## Recommended File Structure

```text
app.py
optimizer/
  data_prep.py
  features.py
  scoring.py
  correlations.py
  scenarios.py
  solver.py
  portfolio.py
  explain.py
  diagnostics.py
  conflicts.py
  regression_checks.py
  calibration.py
  platform_rules.py
utils/
  validation.py
  constants.py
  formatting.py
```

### Module responsibilities
- `data_prep.py` — ingest CSV, normalize fields, infer opposing pitcher hand
- `features.py` — form, order, team-total, platoon features, and canonical percentile helpers such as `player_salary_percentile(...)` and `player_ceiling_percentile(...)`
- `scoring.py` — hitter, pitcher, ceiling, and captain scores
- `correlations.py` — pairwise and stack bonuses
- `scenarios.py` — script-specific adjustments, script modifiers, and preferred-archetype mapping
- `solver.py` — single-lineup optimization
- `portfolio.py` — candidate generation, portfolio assembly, exposure management, and overlap-penalty application
- `explain.py` — user-facing lineup rationale built from deterministic templates and detected reasons
- `diagnostics.py` — why this lineup won/lost, slate-narrowness, and surprise-result analysis
- `conflicts.py` — conflict detection rules, conflict code vocabulary, severity classification, and stack-team aware premium-isolation checks using `detect_conflicts(...)`, `detect_premium_isolation_conflicts(...)`, `get_stack_teams(...)`, and `CONFLICT_CODES`
- `regression_checks.py` — multiplier audits, preset guardrails, and fixture-based validations
- `calibration.py` — slate concentration scoring, team-total damping, salary-spend band evaluation, and review-pool calibration by slate profile
- `platform_rules.py` — DK/FD roster, scoring, and export logic

---

## Build Roadmap

### Version 1
- CSV upload
- data validation
- feature engineering
- scoring engine
- single-lineup solver
- candidate generation
- explanation block
- export schema definitions

### Version 2
- scenario engine
- portfolio assembly
- exposure controls
- captain tiers
- portfolio dashboard
- downloadable exports

### Version 3
- simulation layer
- smarter captain diversification
- optional ownership plugin
- contest-size presets refinement
- richer portfolio optimization
- regression test pack
- diagnostics comparison layer
- cross-slate validation fixtures

---

## What This Does Not Do

To prevent scope creep, the app should explicitly exclude:

- live data fetching
- web scraping
- real-time odds pulls
- player-prop integration
- bankroll management
- contest entry automation
- late-swap automation
- automatic contest selection
- account login / contest-upload bot behavior

Those can be separate future products. They are not part of this blueprint.

---

## Hard Rules

1. Do not pretend ownership exists when it does not.
2. Do not optimize raw projection alone.
3. Do not ignore stack correlation in showdown.
4. Do not use the same scoring logic for hitters and pitchers.
5. Do not let uniqueness bonuses overpower lineup coherence.
6. Do not let tight contest presets default to extreme salary under-spend.
7. Do not generate lineups without explaining them.
8. Do not hide data-quality issues from the user.
9. Do not bury platform rules across the codebase.
10. Do not leave export format implicit.
11. Do not leave portfolio construction algorithmically vague.
12. Do not allow captain multiplier logic to differ between optimization and presentation.

---

## Final Product Goal

The finished app should feel like this:

- honest about the available data
- strong at showdown-specific strategy
- aware of betting context
- aware of lineup correlation
- capable of building tournament-style portfolios
- transparent in why each lineup rated highly
- structured so you can keep improving it without rewriting the entire app

It should feel less like “a CSV optimizer” and more like “a showdown strategy engine with a UI.”
