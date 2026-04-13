"""Streamlit entry point - MLB Showdown GPP Optimizer (V1).

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, sections
"Streamlit App Structure" and "Streamlit execution notes".

Implementation rules (blueprint):
- ``st.cache_data`` for pure data transforms (parsing, features, scoring)
- ``st.session_state`` for mutable optimizer state (locks, excludes,
  generated lineups, solver diagnostics)
- only recompute scores and candidate lineups when CSV or settings change

This V1 implements the core flow: Slate Dashboard, Player Pool, and
Lineups + Review. Tabs 3-5 from the blueprint (Captain Engine, Stack
+ Script Builder, Portfolio Controls) are stubbed for later prompts
so the navigation is in place.
"""

from __future__ import annotations

import io
from typing import Any

import pandas as pd
import streamlit as st

from optimizer.calibration import compute_slate_concentration
from optimizer.data_prep import load_csv, prepare_slate
from optimizer.explain import build_full_lineup_output
from optimizer.platform_rules import (
    DK_SHOWDOWN,
    FD_SINGLE_GAME,
    export_lineups_csv,
    get_platform,
    validate_export,
)
from optimizer.scoring import compute_all_scores
from optimizer.solver import SolverContext, solve_candidate_set
from utils.constants import LINEUP_SCORE_WEIGHTS, PRESETS, SCRIPT_MODIFIERS
from utils.validation import validate_slate_quality


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MLB Showdown GPP Optimizer",
    page_icon=None,
    layout="wide",
)


# ---------------------------------------------------------------------------
# Cached pipeline stages
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _load_and_prepare(csv_bytes: bytes, platform_name: str) -> pd.DataFrame:
    raw = load_csv(io.BytesIO(csv_bytes))
    return prepare_slate(raw, platform_name)


@st.cache_data(show_spinner=False)
def _score_slate(prepared_csv_signature: str, platform_name: str, preset_name: str) -> pd.DataFrame:
    """Score the slate. The signature argument is just a cache key derived
    from the prepared frame (CSV bytes + platform); the actual frame is
    pulled from session_state so we don't roundtrip a DataFrame through
    the cache function arguments."""
    df = st.session_state["_prepared_df"]
    platform = get_platform(platform_name)
    preset = PRESETS[preset_name]
    return compute_all_scores(df, platform, preset)


# ---------------------------------------------------------------------------
# Session-state init
# ---------------------------------------------------------------------------

def _ensure_session_keys() -> None:
    defaults = {
        "_uploaded_bytes": None,
        "_uploaded_name": None,
        "_prepared_df": None,
        "_scored_df": None,
        "locked_player_ids": set(),
        "excluded_player_ids": set(),
        "lineups": [],
        "lineup_settings_signature": None,
        "last_diagnostics": None,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


_ensure_session_keys()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("Settings")

PLATFORM_LABELS = {"DK_SHOWDOWN": "DraftKings Showdown", "FD_SINGLE_GAME": "FanDuel Single-Game"}
platform_name = st.sidebar.selectbox(
    "Platform",
    options=list(PLATFORM_LABELS.keys()),
    format_func=lambda k: PLATFORM_LABELS[k],
    index=0,
)
platform = get_platform(platform_name)

PRESET_LABELS = {"single_entry": "Single-entry", "three_max": "Three-max", "large_mme": "Large MME"}
preset_name = st.sidebar.selectbox(
    "Contest preset",
    options=list(PRESET_LABELS.keys()),
    format_func=lambda k: PRESET_LABELS[k],
    index=0,
)
preset = PRESETS[preset_name]

st.sidebar.caption("Preset defaults are loaded from PRESETS in utils/constants.py.")
with st.sidebar.expander("Preset detail", expanded=False):
    st.json({k: v for k, v in preset.items() if not isinstance(v, dict)})

num_lineups = st.sidebar.slider("Number of lineups", 1, 20, 5)

# Script selector for Stage 1 generation. (Full scenario weighting is
# handled in optimizer/scenarios.py in a later prompt; for now the user
# picks one active script per generation pass.)
script_name = st.sidebar.selectbox(
    "Active script (Stage 1)",
    options=list(SCRIPT_MODIFIERS.keys()),
    index=1,  # fav_controls
)

generate_clicked = st.sidebar.button("Generate Lineups", type="primary")


# ---------------------------------------------------------------------------
# Main header
# ---------------------------------------------------------------------------

st.title("MLB Showdown GPP Optimizer")
st.caption(
    "Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md. "
    "All scoring formulas, presets, and conflict rules read from that document."
)


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_dashboard, tab_pool, tab_captain, tab_stack, tab_portfolio, tab_lineups = st.tabs(
    [
        "1. Slate Dashboard",
        "2. Player Pool",
        "3. Captain Engine",
        "4. Stack + Script",
        "5. Portfolio",
        "6. Lineups + Review",
    ]
)


# ===========================================================================
# Tab 1 - Slate Dashboard
# ===========================================================================

with tab_dashboard:
    st.subheader("Upload slate CSV")
    uploaded = st.file_uploader(
        "Upload a DFF-style slate CSV",
        type=["csv"],
        accept_multiple_files=False,
    )

    if uploaded is not None:
        bytes_now = uploaded.getvalue()
        if (
            st.session_state["_uploaded_bytes"] != bytes_now
            or st.session_state["_uploaded_name"] != uploaded.name
        ):
            # New file -> reset downstream state.
            st.session_state["_uploaded_bytes"] = bytes_now
            st.session_state["_uploaded_name"] = uploaded.name
            st.session_state["lineups"] = []
            st.session_state["lineup_settings_signature"] = None

    if st.session_state["_uploaded_bytes"] is None:
        st.info("Upload a slate CSV to get started. Sample is in test_data/sample_slate.csv.")
    else:
        try:
            prepared = _load_and_prepare(st.session_state["_uploaded_bytes"], platform_name)
        except Exception as exc:
            st.error(f"Failed to parse CSV: {exc}")
            prepared = None

        if prepared is not None:
            st.session_state["_prepared_df"] = prepared

            # Score the slate. Use a stable signature for cache.
            signature = (
                st.session_state["_uploaded_name"],
                platform_name,
                preset_name,
                len(prepared),
            )
            st.session_state["_scored_df"] = _score_slate(
                str(signature), platform_name, preset_name
            )

            scored = st.session_state["_scored_df"]
            slate = scored.iloc[0]

            # Matchup header
            teams = sorted(scored["team"].unique().tolist())
            spreads = scored.groupby("team")["spread"].first().to_dict()
            implied = scored.groupby("team")["implied_team_score"].first().to_dict()
            ou = float(slate["over_under"])

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                if len(teams) == 2:
                    st.metric("Matchup", f"{teams[0]} @ {teams[1]}")
            with c2:
                st.metric("O/U", f"{ou}")
            with c3:
                st.metric(
                    f"{teams[0]} implied",
                    f"{implied.get(teams[0], 0):.2f}",
                    delta=f"spread {spreads.get(teams[0], 0):+}",
                )
            with c4:
                if len(teams) > 1:
                    st.metric(
                        f"{teams[1]} implied",
                        f"{implied.get(teams[1], 0):.2f}",
                        delta=f"spread {spreads.get(teams[1], 0):+}",
                    )

            # Game-script narrative
            fav_team = min(spreads, key=spreads.get) if spreads else None
            dog_team = max(spreads, key=spreads.get) if spreads else None
            if ou >= 9.0:
                env = "high-total slugfest environment"
            elif ou <= 7.5:
                env = "low-total pitcher-friendly environment"
            else:
                env = "moderate-total environment"
            implied_gap = abs(implied.get(teams[0], 0) - implied.get(teams[-1], 0)) if len(teams) > 1 else 0
            shape = "tight team totals" if implied_gap < 0.5 else "clear favorite-side projection"
            st.markdown(
                f"**Game script:** {fav_team} favored over {dog_team} in a {env} with {shape}."
            )

            # Slate quality + concentration
            quality = validate_slate_quality(scored)
            conc = compute_slate_concentration(scored, preset)
            ql, qr = st.columns(2)
            with ql:
                conf = quality.get("confidence", "low")
                color = {"high": "success", "medium": "warning", "low": "error"}[conf]
                getattr(st, color)(f"Slate confidence: {conf.upper()}")
                if quality.get("warnings"):
                    for w in quality["warnings"]:
                        st.caption(f"- {w}")
                else:
                    st.caption("No data warnings.")
            with qr:
                label_color = {"high": "warning", "medium": "info", "low": "info"}[conc["label"]]
                getattr(st, label_color)(
                    f"Slate concentration: {conc['label'].upper()} (score {conc['score']})"
                )
                st.caption(
                    f"captain_gap={conc['captain_gap']}  "
                    f"viable_score={conc['viable_score']}  "
                    f"total_gap={conc['total_gap']}  "
                    f"viable captains={conc['viable_captain_count']}"
                )

            # Platoon summary + form flags
            st.markdown("### Platoon summary")
            platoon_counts = (
                scored[scored["is_hitter"]]
                .groupby(["team", "platoon_edge"])
                .size()
                .unstack(fill_value=0)
            )
            st.dataframe(platoon_counts, width="stretch")

            st.markdown("### Form trend flags")
            form_flags = scored[
                (scored["form_signal"].abs() >= 0.05)
            ][["player_name", "team", "form_signal"]].sort_values("form_signal", ascending=False)
            if len(form_flags):
                form_flags = form_flags.copy()
                form_flags["trend"] = form_flags["form_signal"].apply(
                    lambda x: "hot" if x > 0 else "cold"
                )
                st.dataframe(form_flags, width="stretch", hide_index=True)
            else:
                st.caption("No strong form trends on this slate.")


# ===========================================================================
# Tab 2 - Player Pool
# ===========================================================================

with tab_pool:
    st.subheader("Player pool")
    if st.session_state["_scored_df"] is None:
        st.info("Upload a slate on the Slate Dashboard tab first.")
    else:
        scored = st.session_state["_scored_df"]
        display_cols = [
            "player_name", "team", "position", "salary", "batting_order",
            "median_score", "ceiling_score", "captain_score",
            "value_projection", "platoon_edge", "form_signal",
        ]
        display_df = scored[display_cols].copy().sort_values("captain_score", ascending=False)
        display_df["form_signal"] = display_df["form_signal"].round(3)
        for c in ("median_score", "ceiling_score", "captain_score", "value_projection"):
            display_df[c] = display_df[c].round(3)

        st.dataframe(
            display_df,
            width="stretch",
            hide_index=True,
            column_config={
                "salary": st.column_config.NumberColumn("Salary", format="$%d"),
            },
        )

        st.markdown("### Locks / Excludes")
        cols = st.columns([3, 1, 1])
        cols[0].markdown("**Player**")
        cols[1].markdown("**Lock**")
        cols[2].markdown("**Exclude**")

        for _, row in scored.sort_values("captain_score", ascending=False).iterrows():
            pid = row["player_id"]
            cols = st.columns([3, 1, 1])
            cols[0].write(f"{row['player_name']} ({row['team']})")
            locked = cols[1].checkbox(
                "Lock",
                value=pid in st.session_state["locked_player_ids"],
                key=f"lock_{pid}",
                label_visibility="collapsed",
            )
            excluded = cols[2].checkbox(
                "Exclude",
                value=pid in st.session_state["excluded_player_ids"],
                key=f"excl_{pid}",
                label_visibility="collapsed",
            )
            if locked:
                st.session_state["locked_player_ids"].add(pid)
            else:
                st.session_state["locked_player_ids"].discard(pid)
            if excluded:
                st.session_state["excluded_player_ids"].add(pid)
            else:
                st.session_state["excluded_player_ids"].discard(pid)


# ===========================================================================
# Tab 3 / 4 / 5 - placeholders for later prompts
# ===========================================================================

with tab_captain:
    st.subheader("Captain Engine (Tab 3)")
    st.info("Captain rankings, tiers, and scatterplot land in a later prompt.")

with tab_stack:
    st.subheader("Stack + Script Builder (Tab 4)")
    st.info("Top stack candidates and adjacency clusters land in a later prompt.")

with tab_portfolio:
    st.subheader("Portfolio Controls (Tab 5)")
    st.info("Exposure caps and portfolio diversity controls land in a later prompt.")


# ===========================================================================
# Tab 6 - Lineups + Review
# ===========================================================================

with tab_lineups:
    st.subheader("Lineups + Review")

    if st.session_state["_scored_df"] is None:
        st.info("Upload a slate on the Slate Dashboard tab first.")
    else:
        scored = st.session_state["_scored_df"]

        # Build the settings signature used to know whether to re-solve.
        settings_signature = (
            st.session_state["_uploaded_name"],
            platform_name,
            preset_name,
            int(num_lineups),
            script_name,
            tuple(sorted(st.session_state["locked_player_ids"])),
            tuple(sorted(st.session_state["excluded_player_ids"])),
        )

        if generate_clicked or st.session_state["lineup_settings_signature"] != settings_signature:
            if generate_clicked:
                ctx = SolverContext(
                    script_name=script_name,
                    preset=preset_name,
                    platform=platform_name,
                    stage2_ranking_weights=LINEUP_SCORE_WEIGHTS,
                    script_modifier=SCRIPT_MODIFIERS[script_name],
                    ownership_available="ownership_projection" in scored.columns
                    and scored["ownership_projection"].notna().any(),
                )
                with st.spinner("Solving Stage 1 candidates..."):
                    raw_results = solve_candidate_set(
                        df=scored,
                        platform=platform,
                        preset=preset,
                        solver_context=ctx,
                        num_candidates=int(num_lineups),
                        excluded_players=list(st.session_state["excluded_player_ids"]),
                        locked_players=list(st.session_state["locked_player_ids"]),
                    )
                full_results = [
                    build_full_lineup_output(r, scored, platform, script_name, preset)
                    for r in raw_results
                ]
                st.session_state["lineups"] = full_results
                st.session_state["lineup_settings_signature"] = settings_signature

        lineups = st.session_state["lineups"]
        if not lineups:
            st.info("Click 'Generate Lineups' in the sidebar to produce candidates.")
        else:
            st.success(f"{len(lineups)} candidate lineup(s) generated.")
            for i, ln in enumerate(lineups, start=1):
                title = (
                    f"Lineup #{i} - CPT {ln['captain_name']} ({ln['captain_team']})  "
                    f"| ${ln['total_salary']:,}  |  ceil {ln['ceiling_projection']:.1f}"
                )
                with st.expander(title, expanded=(i == 1)):
                    rows = []
                    cap_id = ln["captain_player_id"]
                    cap_row = scored[scored["player_id"] == cap_id].iloc[0]
                    rows.append({
                        "slot": platform.multiplier_label,
                        "player": cap_row["player_name"],
                        "team": cap_row["team"],
                        "pos": cap_row["position"],
                        "salary": ln["captain_salary"],
                        "median": float(cap_row["median_score"]) * platform.multiplier_points_factor,
                        "ceiling": float(cap_row["ceiling_score"]) * platform.multiplier_points_factor,
                    })
                    for pid in ln["utility_player_ids"]:
                        r = scored[scored["player_id"] == pid].iloc[0]
                        rows.append({
                            "slot": "UTIL",
                            "player": r["player_name"],
                            "team": r["team"],
                            "pos": r["position"],
                            "salary": int(r["salary"]),
                            "median": float(r["median_score"]),
                            "ceiling": float(r["ceiling_score"]),
                        })
                    lineup_df = pd.DataFrame(rows)
                    st.dataframe(
                        lineup_df,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "salary": st.column_config.NumberColumn("Salary", format="$%d"),
                            "median": st.column_config.NumberColumn("Median", format="%.2f"),
                            "ceiling": st.column_config.NumberColumn("Ceiling", format="%.2f"),
                        },
                    )

                    a, b, c, d = st.columns(4)
                    a.metric("Total salary", f"${ln['total_salary']:,}")
                    b.metric("Salary left", f"${ln['salary_left']:,}")
                    c.metric("Ceiling proj", f"{ln['ceiling_projection']:.2f}")
                    d.metric("Correlation", f"{ln['correlation_score']:.2f}")

                    st.markdown("**Explanation**")
                    for line in ln["explanation"]:
                        st.markdown(f"- {line}")

                    if ln["conflicts"]:
                        st.markdown("**Conflict flags**")
                        sev_color = {"soft": "blue", "medium": "orange", "hard": "red"}
                        for c_obj in ln["conflicts"]:
                            color = sev_color.get(c_obj.severity, "gray")
                            st.markdown(
                                f"- :{color}[**[{c_obj.severity.upper()}]**] "
                                f"`{c_obj.code}` - {c_obj.description}"
                            )

            st.markdown("---")
            st.markdown("### Export")
            export_issues = validate_export(lineups, platform)
            if export_issues:
                st.error("Export validation failed:")
                for issue in export_issues:
                    st.markdown(f"- {issue}")
            else:
                csv_text = export_lineups_csv(lineups, platform)
                st.code(csv_text, language="csv")
                site_label = "DK" if platform.name == "DK_SHOWDOWN" else "FD"
                slate_tag = (
                    st.session_state.get("_uploaded_name", "slate")
                    or "slate"
                ).replace(".csv", "")
                st.download_button(
                    label=f"Download {site_label} CSV",
                    data=csv_text,
                    file_name=f"{site_label.lower()}_lineups_{slate_tag}.csv",
                    mime="text/csv",
                    type="primary",
                )
