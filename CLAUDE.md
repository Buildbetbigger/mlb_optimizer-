# MLB Showdown GPP Optimizer

## Source of Truth
The full specification is in MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md. Always reference that document for scoring formulas, constraint logic, preset values, conflict rules, and design decisions. Do not invent defaults — every tunable value has a concrete default in the blueprint.

## Architecture
- optimizer/ — all optimization logic
- utils/ — validation, constants, formatting helpers
- app.py — Streamlit UI entry point

## Key Implementation Rules
- Hitters and pitchers use different scoring logic
- Captain multiplier (1.5x salary, 1.5x points on DK) must be consistent between solver objective and display
- The Stage 1 solver uses the raw expanded objective (median + ceiling_boost + pairwise + stack + captain_stack - conflicts)
- The Stage 2 portfolio ranker uses the normalized weighted final_optimizer_score
- ceiling_boost = ceiling_score - median_score (incremental only, never double-count median)
- Coherence and uniqueness are Stage 2 terms only, not in the Stage 1 solver objective
- Ownership is optional — when absent, hide ownership controls entirely
- Use rank-based percentile normalization for showdown slates (< 30 players)
- Every lineup must include an explanation block

## Testing
- Sample slate CSV is in test_data/sample_slate.csv (TEX @ LAD, O/U 8.5, spread 1.5)
- After building each module, verify against the sample data
