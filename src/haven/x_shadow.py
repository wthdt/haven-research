from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


X_RECENT_COUNTS_URL = "https://api.x.com/2/tweets/counts/recent"


def fetch_x_recent_counts(
    query: str,
    bearer_token: str,
    *,
    granularity: str = "hour",
) -> pd.DataFrame:
    if not bearer_token:
        raise ValueError("X bearer token is required")
    params = urllib.parse.urlencode(
        {"query": query, "granularity": granularity}
    )
    request = urllib.request.Request(
        f"{X_RECENT_COUNTS_URL}?{params}",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
            "User-Agent": "haven-research/0.4",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("data") or []
    if not rows:
        return pd.DataFrame(
            columns=["start", "end", "tweet_count", "query"]
        )
    frame = pd.DataFrame(rows)
    frame["start"] = pd.to_datetime(frame["start"], utc=True)
    frame["end"] = pd.to_datetime(frame["end"], utc=True)
    frame["tweet_count"] = pd.to_numeric(
        frame["tweet_count"], errors="coerce"
    )
    frame["query"] = query
    return frame


def _safe_zscore(current: float, history: pd.Series) -> float:
    clean = history.dropna().astype(float)
    if len(clean) < 2:
        return 0.0
    scale = float(clean.std(ddof=1))
    if scale <= 0.0:
        return 0.0 if current <= float(clean.mean()) else 3.0
    return float((current - float(clean.mean())) / scale)


def score_x_count_history(
    counts: pd.DataFrame,
    *,
    topic: str,
) -> dict[str, Any]:
    if counts.empty:
        return {
            "topic": topic,
            "status": "NO_COUNTS",
            "x_heat": np.nan,
            "coverage": 0.0,
        }
    frame = counts.copy().sort_values("start")
    frame = frame.dropna(subset=["start", "tweet_count"])
    if frame.empty:
        return {
            "topic": topic,
            "status": "NO_COUNTS",
            "x_heat": np.nan,
            "coverage": 0.0,
        }

    end = frame["end"].max()
    recent_start = end - pd.Timedelta(hours=24)
    prior_start = recent_start - pd.Timedelta(hours=24)
    recent = float(
        frame.loc[frame["start"].ge(recent_start), "tweet_count"].sum()
    )
    prior = float(
        frame.loc[
            frame["start"].ge(prior_start)
            & frame["start"].lt(recent_start),
            "tweet_count",
        ].sum()
    )
    daily = (
        frame.set_index("start")["tweet_count"]
        .resample("24h", origin="start_day")
        .sum()
    )
    historical_days = daily.iloc[:-1].tail(6)
    z_score = _safe_zscore(recent, historical_days)
    growth = (recent - prior) / max(prior, 1.0)
    anomaly_score = float(np.clip(50.0 + 15.0 * z_score, 0.0, 100.0))
    growth_score = float(
        np.clip(50.0 + 50.0 * math.tanh(growth), 0.0, 100.0)
    )
    heat = 0.65 * anomaly_score + 0.35 * growth_score
    expected_hours = min(168, max(24, int((end - frame["start"].min()).total_seconds() / 3600)))
    observed_hours = int(frame["start"].nunique())
    coverage = float(np.clip(observed_hours / max(expected_hours, 1), 0.0, 1.0))
    return {
        "topic": topic,
        "status": "COUNTS_ONLY",
        "as_of": str(end),
        "x_heat": float(heat),
        "count_last_24h": recent,
        "count_previous_24h": prior,
        "count_growth_rate": float(growth),
        "count_zscore": float(z_score),
        "coverage": coverage,
        "sentiment_available": False,
        "credibility_available": False,
    }


def build_x_shadow_snapshot(
    topics: list[str],
    bearer_token: str | None,
    *,
    output_dir: Path | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if not bearer_token:
        return (
            {
                "status": "AUTH_NOT_CONFIGURED",
                "x_heat": np.nan,
                "coverage": 0.0,
                "topic_count": int(len(topics)),
                "affects_position": False,
            },
            pd.DataFrame(),
        )

    histories: list[pd.DataFrame] = []
    topic_scores: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for topic in topics:
        try:
            history = fetch_x_recent_counts(topic, bearer_token)
            histories.append(history)
            topic_scores.append(
                score_x_count_history(history, topic=topic)
            )
        except Exception as exc:  # pragma: no cover - authenticated path
            errors.append(
                {
                    "topic": topic,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    score_frame = pd.DataFrame(topic_scores)
    if score_frame.empty:
        summary = {
            "status": "X_DATA_GUARD",
            "x_heat": np.nan,
            "coverage": 0.0,
            "topic_count": int(len(topics)),
            "successful_topic_count": 0,
            "errors": errors,
            "affects_position": False,
        }
    else:
        weights = score_frame["coverage"].fillna(0.0)
        heat = (
            float(
                np.average(
                    score_frame["x_heat"].fillna(50.0),
                    weights=weights,
                )
            )
            if float(weights.sum()) > 0.0
            else np.nan
        )
        coverage = float(
            weights.sum() / max(float(len(topics)), 1.0)
        )
        summary = {
            "status": (
                "X_COUNTS_SHADOW_READY"
                if coverage >= 0.70
                else "X_DATA_GUARD"
            ),
            "x_heat": heat,
            "coverage": coverage,
            "topic_count": int(len(topics)),
            "successful_topic_count": int(len(score_frame)),
            "errors": errors,
            "sentiment_available": False,
            "credibility_available": False,
            "affects_position": False,
        }

    history_frame = (
        pd.concat(histories, ignore_index=True)
        if histories
        else pd.DataFrame()
    )
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        score_frame.to_csv(output_dir / "x_topic_scores.csv", index=False)
        history_frame.to_csv(output_dir / "x_count_history.csv", index=False)
    return summary, score_frame
