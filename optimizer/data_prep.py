"""Data Preparation Layer.

Source of truth: MLB_Showdown_GPP_Optimizer_Blueprint_v3_13.md, section
"Data Preparation Layer" (steps 1-8).

This module:
- loads a raw DFF-style slate CSV and validates required columns
- runs the 8-step preparation pipeline that produces a ready-to-score frame
- surfaces slate-quality metadata (missing orders, pitcher-hand confidence,
  injury warnings) so the UI can expose slate confidence, not just lineups
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from utils.constants import (
    FORM_SIGNAL_CLIP,
    L5_FORM_CLIP,
    L10_FORM_CLIP,
)


REQUIRED_COLUMNS = [
    "first_name",
    "last_name",
    "position",
    "team",
    "salary",
    "hand",
    "spread",
    "over_under",
    "implied_team_score",
    "ppg_projection",
    "value_projection",
    "L5_fppg_avg",
    "L10_fppg_avg",
    "szn_fppg_avg",
]

# At least one of these must be present to identify the opponent team.
OPPONENT_ALIASES = ["opp", "opponent"]

OPTIONAL_COLUMNS = [
    "confirmed_order",
    "starting_pitcher",
    "ownership_projection",
    "injury_status",
]

PITCHER_POSITIONS = {"P", "SP", "RP", "PITCHER"}


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_csv(uploaded_file: Any) -> pd.DataFrame:
    """Load a slate CSV from an uploaded file or path and validate columns.

    Accepts a file-like object (Streamlit upload) or a path string. Raises
    ``ValueError`` with a descriptive message when required columns are
    missing.
    """
    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        raise ValueError(f"Unable to read CSV: {exc}") from exc

    if df.empty:
        raise ValueError("Uploaded CSV is empty.")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            "CSV is missing required columns: " + ", ".join(missing)
        )
    if not any(c in df.columns for c in OPPONENT_ALIASES):
        raise ValueError(
            "CSV is missing an opponent column. Expected one of: "
            + ", ".join(OPPONENT_ALIASES)
        )

    return df


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _norm_hand(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip().upper()


def _is_pitcher_position(pos: Any) -> bool:
    if pos is None or (isinstance(pos, float) and pd.isna(pos)):
        return False
    return any(
        token.strip().upper() in PITCHER_POSITIONS
        for token in str(pos).replace("|", "/").split("/")
    )


def _platoon_edge(hitter_hand: str, opp_pitcher_hand: str | None) -> str:
    """Blueprint section 5 rule.

    - S (switch-hitter) defaults to favorable vs either hand (V1)
    - L vs R or R vs L -> favorable
    - same-handed -> neutral
    - missing opposing-pitcher hand -> neutral (conservative default)
    """
    if hitter_hand == "S":
        return "favorable"
    if not opp_pitcher_hand:
        return "neutral"
    if hitter_hand and hitter_hand != opp_pitcher_hand:
        return "favorable"
    return "neutral"


def _clip(series: pd.Series, lo: float, hi: float) -> pd.Series:
    return series.clip(lower=lo, upper=hi)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def prepare_slate(df: pd.DataFrame, platform_name: str) -> pd.DataFrame:
    """Run the 8-step data preparation pipeline from the blueprint.

    Parameters
    ----------
    df
        Raw slate DataFrame (post-``load_csv``).
    platform_name
        Platform identifier (``"DK_SHOWDOWN"`` or ``"FD_SINGLE_GAME"``).
        Retained on every row for downstream platform-aware logic.
    """
    out = df.copy().reset_index(drop=True)
    out["platform_name"] = platform_name

    # --- 1. Normalize identity ---------------------------------------------
    out["player_name"] = (
        out["first_name"].astype(str).str.strip()
        + " "
        + out["last_name"].astype(str).str.strip()
    )
    out["player_id"] = [f"p{i:04d}" for i in range(len(out))]

    # --- 2. Normalize role -------------------------------------------------
    out["is_pitcher"] = out["position"].apply(_is_pitcher_position)
    out["is_hitter"] = ~out["is_pitcher"]

    if "starting_pitcher" in out.columns:
        starting_flag = out["starting_pitcher"].astype(str).str.strip().str.lower()
        is_starting = starting_flag.isin(
            {"true", "yes", "y", "1", "sp", "starter"}
        )
    else:
        is_starting = pd.Series(False, index=out.index)
    out["is_starting_pitcher"] = out["is_pitcher"] & is_starting

    # If no explicit starter flags survive, treat the highest-projection
    # pitcher on each team as the inferred starter. Showdown slates normally
    # list exactly one starter per side.
    if not out["is_starting_pitcher"].any() and out["is_pitcher"].any():
        for _team, team_pitchers in out[out["is_pitcher"]].groupby("team"):
            top_idx = team_pitchers["ppg_projection"].astype(float).idxmax()
            out.at[top_idx, "is_starting_pitcher"] = True

    # --- 3. Normalize batting order ----------------------------------------
    if "confirmed_order" in out.columns:
        orders = pd.to_numeric(out["confirmed_order"], errors="coerce")
    else:
        orders = pd.Series(np.nan, index=out.index)

    batting_order = np.where(
        out["is_pitcher"],
        0,
        orders.fillna(-1).astype(int),
    )
    out["batting_order"] = batting_order.astype(int)

    # --- 4. Infer opposing pitcher hand ------------------------------------
    out["hand"] = out["hand"].apply(_norm_hand)
    out["team"] = out["team"].astype(str).str.strip().str.upper()

    # Accept either ``opp`` or ``opponent`` as the opponent column.
    opp_source = "opp" if "opp" in out.columns else "opponent"
    out["opp"] = out[opp_source].astype(str).str.strip().str.upper()
    out["opponent"] = out["opp"]

    starting_pitchers = out[out["is_starting_pitcher"]]
    hand_by_team = dict(
        zip(starting_pitchers["team"], starting_pitchers["hand"])
    )
    out["opp_pitcher_hand"] = out["opp"].map(hand_by_team)

    # --- 5. Create platoon flags ------------------------------------------
    out["platoon_edge"] = [
        _platoon_edge(h, o) if is_hit else "neutral"
        for h, o, is_hit in zip(
            out["hand"], out["opp_pitcher_hand"], out["is_hitter"]
        )
    ]

    # --- 6. Create game metadata -------------------------------------------
    out["spread"] = pd.to_numeric(out["spread"], errors="coerce")
    out["over_under"] = pd.to_numeric(out["over_under"], errors="coerce")
    out["implied_team_score"] = pd.to_numeric(
        out["implied_team_score"], errors="coerce"
    )

    out["game_total"] = out["over_under"]
    out["team_total"] = out["implied_team_score"]
    out["is_favorite"] = out["spread"] < 0
    out["is_underdog"] = out["spread"] > 0

    # --- 7. Create restrained form deltas ---------------------------------
    l5 = pd.to_numeric(out["L5_fppg_avg"], errors="coerce").fillna(0.0)
    l10 = pd.to_numeric(out["L10_fppg_avg"], errors="coerce").fillna(0.0)
    szn = pd.to_numeric(out["szn_fppg_avg"], errors="coerce").fillna(0.0)

    denom = szn.where(szn > 1.0, 1.0)
    l5_delta = _clip((l5 - szn) / denom, -L5_FORM_CLIP, L5_FORM_CLIP)
    l10_delta = _clip((l10 - szn) / denom, -L10_FORM_CLIP, L10_FORM_CLIP)

    form_signal = 0.55 * l5_delta + 0.45 * l10_delta
    out["form_signal"] = _clip(form_signal, -FORM_SIGNAL_CLIP, FORM_SIGNAL_CLIP)

    # Numeric hygiene for downstream scoring modules.
    out["salary"] = pd.to_numeric(out["salary"], errors="coerce").astype("Int64")
    out["ppg_projection"] = pd.to_numeric(
        out["ppg_projection"], errors="coerce"
    ).fillna(0.0)
    out["value_projection"] = pd.to_numeric(
        out["value_projection"], errors="coerce"
    ).fillna(0.0)
    if "ownership_projection" in out.columns:
        out["ownership_projection"] = pd.to_numeric(
            out["ownership_projection"], errors="coerce"
        )

    return out


# ---------------------------------------------------------------------------
# Slate quality metadata (step 8)
# ---------------------------------------------------------------------------

def get_slate_quality(df: pd.DataFrame) -> dict:
    """Return slate-quality metadata after ``prepare_slate``.

    Keys:
        warnings                 -- list[str], human-readable issues
        confidence               -- "high" | "medium" | "low"
        missing_orders           -- int, hitters with no confirmed order
        pitcher_hand_confidence  -- "high" | "medium" | "low"
        injury_warnings          -- list[str]
        generated_at             -- UTC ISO-8601 timestamp
    """
    warnings: list[str] = []

    # Missing batting orders (step 3 rule) -----------------------------------
    hitters = df[df["is_hitter"]] if "is_hitter" in df.columns else df.iloc[0:0]
    missing_orders = (
        int((hitters["batting_order"] <= 0).sum()) if not hitters.empty else 0
    )
    if missing_orders:
        warnings.append(
            f"{missing_orders} hitter(s) missing confirmed batting order"
        )

    # Pitcher-hand inference confidence --------------------------------------
    if "is_starting_pitcher" in df.columns:
        starters = df[df["is_starting_pitcher"]]
        teams = df["team"].nunique() if "team" in df.columns else 0
        starter_count = len(starters)
        hand_known = (
            starters["hand"].apply(lambda h: h in {"L", "R", "S"}).sum()
            if starter_count
            else 0
        )
        if teams and starter_count == teams and hand_known == starter_count:
            pitcher_hand_confidence = "high"
        elif starter_count >= 1 and hand_known >= 1:
            pitcher_hand_confidence = "medium"
        else:
            pitcher_hand_confidence = "low"
            warnings.append(
                "Unable to infer opposing pitcher hand on one or both sides"
            )
    else:
        pitcher_hand_confidence = "low"
        warnings.append("No starting-pitcher flags available on slate")

    # Injury / scratch warnings ---------------------------------------------
    injury_warnings: list[str] = []
    if "injury_status" in df.columns:
        bad = {"OUT", "IL", "IL10", "IL15", "IL60", "D", "GTD", "DTD", "Q"}
        for _, row in df.iterrows():
            status = str(row.get("injury_status", "")).strip().upper()
            if status and status not in {"NA", "NAN", "NONE", "ACTIVE", ""}:
                if status in bad or status.startswith("IL"):
                    injury_warnings.append(
                        f"{row.get('player_name', row.get('player_id', '?'))}: {status}"
                    )
    if injury_warnings:
        warnings.append(
            f"{len(injury_warnings)} player(s) flagged with injury status"
        )

    # Overall confidence label ----------------------------------------------
    if not warnings and pitcher_hand_confidence == "high":
        confidence = "high"
    elif pitcher_hand_confidence == "low" or missing_orders >= 3:
        confidence = "low"
    else:
        confidence = "medium"

    return {
        "warnings": warnings,
        "confidence": confidence,
        "missing_orders": missing_orders,
        "pitcher_hand_confidence": pitcher_hand_confidence,
        "injury_warnings": injury_warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
