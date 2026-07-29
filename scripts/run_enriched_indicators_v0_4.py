#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
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

from haven.backtest import (  # noqa: E402
    performance_metrics,
    run_strategy_backtest,
    stress_period_metrics,
)
from haven.data import build_research_dataset  # noqa: E402
from haven.enriched import (  # noqa: E402
    attach_enriched_market_data,
    build_enriched_scores,
)
from haven.model import build_scores, run_state_machine  # noqa: E402
from haven.options_proxy import run_options_proxy_backtest  # noqa: E402


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_config() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
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
    v0_3_config = copy.deepcopy(config)
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
    return config, enriched, v0_3_config


def _metric_row(
    name: str,
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    model_layer: str,
    subperiod: str = "full",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "strategy": name,
        "model_layer": model_layer,
        "subperiod": subperiod,
    }
    row.update(performance_metrics(frame, config))
    return row


def _slice_frame(
    frame: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    return frame.loc[start:end].copy()


def _state_summary(name: str, states: pd.DataFrame) -> pd.DataFrame:
    counts = states["state"].value_counts(dropna=False).rename("days")
    result = counts.rename_axis("state").reset_index()
    result.insert(0, "strategy", name)
    result["share"] = result["days"] / max(int(result["days"].sum()), 1)
    return result


def _stress_rows(
    name: str,
    frame: pd.DataFrame,
    *,
    model_layer: str,
) -> pd.DataFrame:
    result = stress_period_metrics(frame)
    result.insert(0, "model_layer", model_layer)
    result.insert(0, "strategy", name)
    return result


def _score_diagnostics(
    base_scores: pd.DataFrame,
    enriched_scores: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]
    base = base_scores.loc[start:end]
    enriched = enriched_scores.loc[start:end]
    rows: list[dict[str, Any]] = []
    thresholds = {
        "P": float(config["thresholds"]["panic_trigger"]),
        "S": float(config["thresholds"]["stabilization_2"]),
        "G": float(config["thresholds"]["greed"]),
        "E": float(config["thresholds"]["exhaustion"]),
    }
    for code in ["P", "S", "G", "E"]:
        difference = enriched[code] - base[code]
        threshold = thresholds[code]
        rows.append(
            {
                "model": code,
                "base_mean": float(base[code].mean()),
                "enriched_mean": float(enriched[code].mean()),
                "mean_change": float(difference.mean()),
                "mean_absolute_change": float(difference.abs().mean()),
                "maximum_absolute_change": float(difference.abs().max()),
                "base_days_above_threshold": int(
                    base[code].ge(threshold).sum()
                ),
                "enriched_days_above_threshold": int(
                    enriched[code].ge(threshold).sum()
                ),
                "threshold_disagreement_days": int(
                    (
                        base[code].ge(threshold)
                        != enriched[code].ge(threshold)
                    ).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    config, overlay, v0_3_config = _load_config()
    output_dir = PROJECT_ROOT / "outputs" / "ten_year_v0_4_enriched"
    output_dir.mkdir(parents=True, exist_ok=True)

    data, base_metadata = build_research_dataset(config, PROJECT_ROOT)
    data, enriched_metadata = attach_enriched_market_data(
        data, config, PROJECT_ROOT
    )
    ungated_config = copy.deepcopy(config)
    ungated_config["enriched_scores"]["recovery_gate"]["enabled"] = False
    base_scores, _ = build_scores(data, config)
    base_states = run_state_machine(base_scores, ungated_config)

    score_jobs: dict[str, set[str]] = {
        "all": set(),
        "no_breadth": {"breadth"},
        "no_liquidity": {"liquidity"},
        "no_tail": {"tail"},
    }
    enriched_scores: dict[str, pd.DataFrame] = {}
    for name, disabled in score_jobs.items():
        scores, _ = build_enriched_scores(
            data,
            config,
            disabled_overlays=disabled,
        )
        enriched_scores[name] = scores
    enriched_states: dict[str, pd.DataFrame] = {
        "v0_4_all": run_state_machine(enriched_scores["all"], config),
        "v0_4_all_ungated": run_state_machine(
            enriched_scores["all"], ungated_config
        ),
        "v0_4_no_breadth_ungated": run_state_machine(
            enriched_scores["no_breadth"], ungated_config
        ),
        "v0_4_no_liquidity_ungated": run_state_machine(
            enriched_scores["no_liquidity"], ungated_config
        ),
        "v0_4_no_tail_ungated": run_state_machine(
            enriched_scores["no_tail"], ungated_config
        ),
    }

    split = config["capital_splits"]["split_35_20_45"]
    ps_frames: dict[str, pd.DataFrame] = {
        "v0_3_base_scores": run_strategy_backtest(
            data, base_states, "QQQ", split, config
        )
    }
    for name, states in enriched_states.items():
        ps_frames[name] = run_strategy_backtest(
            data, states, "QQQ", split, config
        )

    metric_rows: list[dict[str, Any]] = []
    stress_rows: list[pd.DataFrame] = []
    subperiods = {
        "first_half": ("2016-07-25", "2020-12-31"),
        "second_half": ("2021-01-01", "2026-07-24"),
    }
    for name, frame in ps_frames.items():
        metric_rows.append(
            _metric_row(name, frame, config, model_layer="PS_only")
        )
        stress_rows.append(
            _stress_rows(name, frame, model_layer="PS_only")
        )
        for subperiod, (start, end) in subperiods.items():
            metric_rows.append(
                _metric_row(
                    name,
                    _slice_frame(frame, start, end),
                    config,
                    model_layer="PS_only",
                    subperiod=subperiod,
                )
            )

    full_frames: dict[str, pd.DataFrame] = {}
    full_trades: dict[str, pd.DataFrame] = {}
    option_summaries: list[dict[str, Any]] = []
    for name, states, run_config, option_overrides in [
        (
            "v0_3_full_base_scores",
            base_states,
            v0_3_config,
            None,
        ),
        (
            "v0_4_full_enriched_ungated",
            enriched_states["v0_4_all_ungated"],
            config,
            None,
        ),
        (
            "v0_4_full_no_csp_e_gate",
            enriched_states["v0_4_all"],
            config,
            {"cash_secured_put": {"entry_e_max": 101.0}},
        ),
        (
            "v0_4_full_enriched_scores",
            enriched_states["v0_4_all"],
            config,
            None,
        ),
    ]:
        frame, trades, summary = run_options_proxy_backtest(
            data,
            states,
            "QQQ",
            split,
            run_config,
            option_overrides=option_overrides,
            strategy_name=name,
        )
        full_frames[name] = frame
        full_trades[name] = trades
        option_summaries.append(summary)
        metric_rows.append(
            _metric_row(name, frame, config, model_layer="full_proxy")
        )
        stress_rows.append(
            _stress_rows(name, frame, model_layer="full_proxy")
        )
        for subperiod, (start, end) in subperiods.items():
            metric_rows.append(
                _metric_row(
                    name,
                    _slice_frame(frame, start, end),
                    config,
                    model_layer="full_proxy",
                    subperiod=subperiod,
                )
            )

    module_jobs = {
        "PS_proxy": {
            "covered_call": False,
            "insurance": False,
            "cash_secured_put": False,
        },
        "Call_only": {
            "covered_call": True,
            "insurance": False,
            "cash_secured_put": False,
        },
        "CSP_only": {
            "covered_call": False,
            "insurance": False,
            "cash_secured_put": True,
        },
        "Insurance_only": {
            "covered_call": False,
            "insurance": True,
            "cash_secured_put": False,
        },
    }
    module_metric_rows: list[dict[str, Any]] = []
    module_stress_rows: list[pd.DataFrame] = []
    module_summaries: list[dict[str, Any]] = []
    for module_name, modules in module_jobs.items():
        frame, _, summary = run_options_proxy_backtest(
            data,
            enriched_states["v0_4_all"],
            "QQQ",
            split,
            config,
            modules=modules,
            strategy_name=f"v0_4_{module_name}",
        )
        row = {"strategy": module_name}
        row.update(performance_metrics(frame, config))
        module_metric_rows.append(row)
        stress = stress_period_metrics(frame)
        stress.insert(0, "strategy", module_name)
        module_stress_rows.append(stress)
        module_summaries.append(summary)
    final_full = full_frames["v0_4_full_enriched_scores"]
    final_summary = next(
        item
        for item in option_summaries
        if item["strategy"] == "v0_4_full_enriched_scores"
    )
    final_row = {"strategy": "Full"}
    final_row.update(performance_metrics(final_full, config))
    module_metric_rows.append(final_row)
    final_stress = stress_period_metrics(final_full)
    final_stress.insert(0, "strategy", "Full")
    module_stress_rows.append(final_stress)
    module_summaries.append(final_summary)

    metrics = pd.DataFrame(metric_rows)
    stresses = pd.concat(stress_rows, ignore_index=True)
    state_counts = pd.concat(
        [
            _state_summary("v0_3_base_scores", base_states),
            *[
                _state_summary(name, states)
                for name, states in enriched_states.items()
            ],
        ],
        ignore_index=True,
    )
    score_diagnostics = _score_diagnostics(
        base_scores, enriched_scores["all"], config
    )

    latest_columns = [
        "P_base",
        "P",
        "P_coverage",
        "S_base",
        "S",
        "S_coverage",
        "G_base",
        "G",
        "G_coverage",
        "E_base",
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
    score_history = enriched_scores["all"].loc[
        config["strategy"]["evaluation_start"] :
        config["strategy"]["evaluation_end"],
        latest_columns,
    ]
    latest = score_history.tail(1).T.reset_index()
    latest.columns = ["metric", "value"]
    latest.insert(
        0,
        "date",
        str(score_history.index[-1].date()),
    )

    metrics.to_csv(output_dir / "metrics.csv", index=False)
    stresses.to_csv(output_dir / "stress_periods.csv", index=False)
    state_counts.to_csv(output_dir / "state_counts.csv", index=False)
    score_diagnostics.to_csv(
        output_dir / "score_diagnostics.csv", index=False
    )
    score_history.to_csv(output_dir / "enriched_score_history.csv")
    latest.to_csv(output_dir / "latest_scores.csv", index=False)
    pd.DataFrame(option_summaries).to_csv(
        output_dir / "option_summary.csv", index=False
    )
    pd.DataFrame(module_metric_rows).to_csv(
        output_dir / "options_module_ablation_metrics.csv",
        index=False,
    )
    pd.concat(module_stress_rows, ignore_index=True).to_csv(
        output_dir / "options_module_ablation_stress.csv",
        index=False,
    )
    pd.DataFrame(module_summaries).to_csv(
        output_dir / "options_module_ablation_summary.csv",
        index=False,
    )
    full_frames["v0_4_full_enriched_scores"].to_csv(
        output_dir / "v0_4_full_daily.csv"
    )
    full_trades["v0_4_full_enriched_scores"].to_csv(
        output_dir / "v0_4_option_trades.csv", index=False
    )

    comparison = metrics[
        metrics["subperiod"].eq("full")
        & metrics["strategy"].isin(
            [
                "v0_3_base_scores",
                "v0_4_all",
                "v0_4_all_ungated",
                "v0_3_full_base_scores",
                "v0_4_full_enriched_ungated",
                "v0_4_full_enriched_scores",
            ]
        )
    ].copy()
    comparison.to_csv(output_dir / "headline_comparison.csv", index=False)

    manifest = {
        "version": "v0.4 enriched indicators",
        "research_only": True,
        "evaluation_start": config["strategy"]["evaluation_start"],
        "evaluation_end": config["strategy"]["evaluation_end"],
        "trading_days": int(
            len(
                data.loc[
                    config["strategy"]["evaluation_start"] :
                    config["strategy"]["evaluation_end"]
                ]
            )
        ),
        "models": ["P", "S", "G", "E", "R", "I", "N"],
        "R_submodels": ["R_put", "R_call"],
        "N_mode": "shadow_only",
        "historical_real_option_chain_used": False,
        "live_chain_backfilled_into_history": False,
        "paper_or_live_enabled": False,
        "tqqq_paper_live_allowed": False,
        "signal_timing": "close t; effective t+1",
        "ablations": [
            "recovery_gate",
            "breadth_score",
            "liquidity_score",
            "tail_score",
        ],
        "base_data_metadata": base_metadata,
        "enriched_data_metadata": enriched_metadata,
        "guardrails": overlay["guardrails"],
        "promotion": overlay["enriched_scores"]["promotion"],
        "automatic_promotion": False,
        "selection_note": (
            "No overlay is promoted automatically. Full-period, both "
            "subperiods, named stress windows, coverage and decision changes "
            "must be reviewed together."
        ),
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    headline = comparison[
        [
            "strategy",
            "model_layer",
            "cagr",
            "sharpe_excess_bil",
            "max_drawdown",
            "average_asset_weight",
        ]
    ]
    print(headline.to_string(index=False), flush=True)
    print(
        "\nLatest enriched scores:\n"
        + latest.to_string(index=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
