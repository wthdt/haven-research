#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from haven.data import build_research_dataset  # noqa: E402
from haven.enriched import (  # noqa: E402
    attach_enriched_market_data,
    build_enriched_scores,
    build_news_shadow_score,
)
from haven.live_market import (  # noqa: E402
    build_live_breadth_snapshot,
    fetch_nasdaq_qqq_option_chain,
    summarize_option_chain,
)
from haven.model import run_state_machine  # noqa: E402
from haven.x_shadow import build_x_shadow_snapshot  # noqa: E402


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_config(end_date: str) -> dict[str, Any]:
    base = yaml.safe_load(
        (
            PROJECT_ROOT / "config" / "haven_v0_2_options_proxy.yaml"
        ).read_text(encoding="utf-8")
    )
    insurance = yaml.safe_load(
        (
            PROJECT_ROOT / "config" / "haven_v0_3_insurance_model.yaml"
        ).read_text(encoding="utf-8")
    )
    enriched = yaml.safe_load(
        (
            PROJECT_ROOT
            / "config"
            / "haven_v0_4_enriched_indicators.yaml"
        ).read_text(encoding="utf-8")
    )
    config = _merge(base, {"options_proxy": insurance["options_proxy"]})
    config = _merge(
        config,
        {
            "strategy": enriched["strategy"],
            "data": enriched["data"],
            "enriched_data": enriched["enriched_data"],
            "enriched_scores": enriched["enriched_scores"],
            "options_proxy": enriched["options_proxy"],
            "live_shadow": enriched["live_shadow"],
            "guardrails": enriched["guardrails"],
        },
    )
    config["strategy"]["evaluation_end"] = end_date
    return config


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if value is pd.NA:
        return None
    return value


def _score_snapshot(
    scores: pd.DataFrame,
    states: pd.DataFrame,
) -> dict[str, Any]:
    required = ["P", "S", "G", "E"]
    valid = scores.dropna(subset=required)
    if valid.empty:
        return {
            "status": "DATA_GUARD",
            "reason": "No complete P/S/G/E row",
        }
    date = valid.index[-1]
    score = valid.loc[date]
    state = states.loc[date]
    columns = [
        "P",
        "P_coverage",
        "S",
        "S_coverage",
        "G",
        "G_coverage",
        "E",
        "E_coverage",
        "R",
        "R_coverage",
        "R_put",
        "R_put_coverage",
        "R_call",
        "R_call_coverage",
        "I",
        "I_need",
        "I_affordability",
        "breadth_stress",
        "breadth_recovery",
        "liquidity_stress",
        "liquidity_relief",
        "tail_stress",
    ]
    values = {
        column: score.get(column, np.nan)
        for column in columns
    }
    return {
        "status": str(state["state"]),
        "signal_date": str(date.date()),
        "effective_timing": "next trading day",
        "event_active": bool(state["event_active"]),
        "deployment_fraction": float(state["deployment_fraction"]),
        "scores": values,
    }


def _append_history(path: Path, row: dict[str, Any]) -> None:
    frame = pd.DataFrame([row])
    if path.exists():
        existing = pd.read_csv(path)
        frame = pd.concat([existing, frame], ignore_index=True)
    dedupe_columns = [
        column
        for column in ["score_signal_date", "option_as_of", "breadth_as_of"]
        if column in frame
    ]
    if dedupe_columns:
        frame = frame.drop_duplicates(dedupe_columns, keep="last")
    frame.to_csv(path, index=False)


def _serialize_components(
    components: dict[str, pd.DataFrame],
) -> dict[str, dict[str, float]]:
    """Extract the latest component values as a JSON-safe dict.

    Each component group (e.g. 'panic', 'greed') maps to a DataFrame
    whose columns are component names.  The last row is serialised as
    ``{column_name: value}``.  NaN → null for JSON cleanliness.
    """
    result: dict[str, dict[str, float]] = {}
    for group, frame in components.items():
        if frame.empty:
            continue
        last = frame.iloc[-1]
        result[group] = {
            col: (None if pd.isna(val) else float(val))
            for col, val in last.items()
        }
    return result


def main() -> None:
    now_et = pd.Timestamp.now(tz="America/New_York")
    end_date = str(now_et.date())
    config = _load_config(end_date)
    force_market = os.environ.get(
        "HAVEN_FORCE_MARKET_DATA", "1"
    ) not in {"0", "false", "False"}
    force_breadth = os.environ.get(
        "HAVEN_FORCE_BREADTH", "1"
    ) not in {"0", "false", "False"}
    force_chain = os.environ.get(
        "HAVEN_FORCE_OPTION_CHAIN", "1"
    ) not in {"0", "false", "False"}
    network_config = config["live_shadow"]["network"]
    request_timeout_seconds = float(
        network_config["request_timeout_seconds"]
    )
    request_retries = max(
        1, int(network_config["request_retries"])
    )
    breadth_batch_timeout_seconds = float(
        network_config["breadth_batch_timeout_seconds"]
    )
    breadth_max_workers = max(
        1, int(network_config["breadth_max_workers"])
    )
    output_dir = (
        PROJECT_ROOT / "outputs" / "ten_year_v0_4_enriched" / "live"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = PROJECT_ROOT / "data" / "raw"

    snapshot: dict[str, Any] = {
        "generated_at_et": str(now_et),
        "research_only": True,
        "affects_paper_or_live": False,
        "affects_positions": False,
        "network_policy": {
            "request_timeout_seconds": request_timeout_seconds,
            "request_retries": request_retries,
            "breadth_batch_timeout_seconds": (
                breadth_batch_timeout_seconds
            ),
            "breadth_max_workers": breadth_max_workers,
            "timeout_behavior": "DATA_GUARD",
        },
    }

    try:
        data, base_metadata = build_research_dataset(
            config,
            PROJECT_ROOT,
            force=force_market,
            request_retries=request_retries,
            request_timeout_seconds=request_timeout_seconds,
        )
        data, enriched_metadata = attach_enriched_market_data(
            data,
            config,
            PROJECT_ROOT,
            force=force_market,
            request_retries=request_retries,
            request_timeout_seconds=request_timeout_seconds,
        )
        scores, components, audit = build_enriched_scores(
            data,
            config,
            include_audit=True,
        )
        states = run_state_machine(scores, config)
        snapshot["model"] = _score_snapshot(scores, states)
        snapshot["calculation_audit"] = _clean_json(audit)
        snapshot["data_metadata"] = {
            "base": base_metadata,
            "enriched": enriched_metadata,
        }
    except Exception as exc:  # pragma: no cover - live network path
        data = pd.DataFrame()
        snapshot["model"] = {
            "status": "DATA_GUARD",
            "error": f"{type(exc).__name__}: {exc}",
        }

    breadth_config = config["live_shadow"]["real_breadth"]
    try:
        breadth, breadth_detail = build_live_breadth_snapshot(
            raw_dir,
            force=force_breadth,
            minimum_member_coverage=float(
                breadth_config["minimum_member_coverage"]
            ),
            max_workers=breadth_max_workers,
            request_retries=request_retries,
            request_timeout_seconds=request_timeout_seconds,
            batch_timeout_seconds=breadth_batch_timeout_seconds,
        )
        breadth_detail.to_csv(
            output_dir / "nasdaq100_breadth_detail.csv",
            index=False,
        )
        snapshot["real_breadth_shadow"] = breadth
    except Exception as exc:  # pragma: no cover - live network path
        snapshot["real_breadth_shadow"] = {
            "status": "LIVE_SHADOW_DATA_GUARD",
            "error": f"{type(exc).__name__}: {exc}",
            "historical_backtest_eligible": False,
        }

    chain_config = config["live_shadow"]["qqq_option_chain"]
    try:
        chain_metadata, chain = fetch_nasdaq_qqq_option_chain(
            raw_dir,
            force=force_chain,
            request_retries=request_retries,
            request_timeout_seconds=request_timeout_seconds,
        )
        if data.empty:
            rate = 0.04
            dividend_yield = 0.005
        else:
            latest_market = data.dropna(subset=["qqq_close"]).iloc[-1]
            rate = float(latest_market["cash_yield_pct"]) / 100.0
            trailing_dividend = (
                data["qqq_dividend"].rolling(252, min_periods=20).sum()
            )
            dividend_yield = float(
                trailing_dividend.loc[latest_market.name]
                / latest_market["qqq_close"]
            )
        chain_summary, enriched_chain = summarize_option_chain(
            chain,
            rate=rate,
            dividend_yield=dividend_yield,
            target_dte_short=int(chain_config["target_dte_short"]),
            target_dte_long=int(chain_config["target_dte_long"]),
            target_put_delta=float(chain_config["target_put_delta"]),
        )
        enriched_chain.to_csv(
            output_dir / "qqq_option_chain_snapshot.csv",
            index=False,
        )
        snapshot["real_qqq_chain_shadow"] = (
            chain_metadata | chain_summary
        )
    except Exception as exc:  # pragma: no cover - live network path
        snapshot["real_qqq_chain_shadow"] = {
            "status": "LIVE_CHAIN_DATA_GUARD",
            "error": f"{type(exc).__name__}: {exc}",
            "historical_backtest_eligible": False,
        }

    news_config = config["live_shadow"]["news_model"]
    x_summary, x_topics = build_x_shadow_snapshot(
        list(news_config["x_topics"]),
        os.environ.get("X_BEARER_TOKEN"),
        output_dir=output_dir,
    )
    x_topics.to_csv(output_dir / "x_topic_scores.csv", index=False)
    news_inputs = (
        {"x_heat": x_summary["x_heat"]}
        if np.isfinite(x_summary.get("x_heat", np.nan))
        else {}
    )
    news_score = build_news_shadow_score(news_inputs)
    snapshot["N_news_shadow"] = {
        **news_score,
        "x": x_summary,
        "minimum_live_days_before_review": int(
            news_config["minimum_live_days_before_review"]
        ),
    }

    clean = _clean_json(snapshot)
    (output_dir / "current_snapshot.json").write_text(
        json.dumps(
            clean,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    model = clean.get("model", {})
    model_scores = model.get("scores", {})
    breadth = clean.get("real_breadth_shadow", {})
    option = clean.get("real_qqq_chain_shadow", {})
    history_row = {
        "generated_at_et": clean["generated_at_et"],
        "score_signal_date": model.get("signal_date"),
        "state": model.get("status"),
        "P": model_scores.get("P"),
        "S": model_scores.get("S"),
        "G": model_scores.get("G"),
        "E": model_scores.get("E"),
        "R_put_proxy": model_scores.get("R_put"),
        "R_call_proxy": model_scores.get("R_call"),
        "I": model_scores.get("I"),
        "breadth_as_of": breadth.get("as_of"),
        "percent_above_ma20": breadth.get("percent_above_ma20"),
        "percent_above_ma50": breadth.get("percent_above_ma50"),
        "percent_above_ma200": breadth.get("percent_above_ma200"),
        "advance_decline_net": breadth.get("advance_decline_net"),
        "option_as_of": option.get("as_of"),
        "option_30d_atm_iv": (
            (option.get("short_tenor") or {}).get("atm_iv")
        ),
        "option_30d_put_skew": (
            (option.get("short_tenor") or {}).get(
                "put_skew_vol_points"
            )
        ),
        "option_term_slope": option.get(
            "iv_term_slope_vol_points"
        ),
        "option_relative_spread": option.get(
            "median_relative_bid_ask_spread"
        ),
        "N": (clean.get("N_news_shadow") or {}).get("N"),
        "N_status": (clean.get("N_news_shadow") or {}).get(
            "N_status"
        ),
    }
    _append_history(output_dir / "live_shadow_history.csv", history_row)
    print(
        json.dumps(
            {
                "model": clean.get("model"),
                "real_breadth_shadow": clean.get(
                    "real_breadth_shadow"
                ),
                "real_qqq_chain_shadow": clean.get(
                    "real_qqq_chain_shadow"
                ),
                "N_news_shadow": clean.get("N_news_shadow"),
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
