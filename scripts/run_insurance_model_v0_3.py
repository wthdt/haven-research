#!/usr/bin/env python3
from __future__ import annotations

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

from haven.backtest import performance_metrics, stress_period_metrics  # noqa: E402
from haven.data import build_research_dataset  # noqa: E402
from haven.model import build_scores, run_state_machine  # noqa: E402
from haven.options_proxy import run_options_proxy_backtest  # noqa: E402


def _load_config() -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / "haven_v0_2_options_proxy.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _insurance_base() -> dict[str, Any]:
    return {
        "use_insurance_score": True,
        "allowed_entry_states": [
            "RISK_WARNING",
            "GREED_EXHAUSTING",
            "NORMAL_PARTICIPATION",
            "RANGE_CARRY",
        ],
        "trigger_i_need_min": 55.0,
        "trigger_i_min": 48.0,
        "trigger_i_affordability_min": 10.0,
        "entry_r_proxy_max": 90.0,
        "dynamic_coverage_enabled": False,
        "coverage_fraction": 1.0,
        "minimum_hold_calendar_days": 75,
        "recovery_s_min": 101.0,
        "recovery_requires_event_inactive": True,
        "risk_gone_p_max": 15.0,
        "risk_gone_confirmation_days": 15,
        "alert_memory_trading_days": 20,
        "preserve_residual_while_risk_active": True,
        "minimum_residual_fraction": 0.50,
        "minimum_residual_contracts": 1,
        "first_total_close_fraction": 0.20,
        "second_total_close_fraction": 0.50,
    }


def _candidate_specs() -> list[dict[str, Any]]:
    budgets = [0.003, 0.004, 0.006, 0.008, 0.010, 0.012]
    structures = [
        {
            "structure": "protective_put",
            "target_calendar_dte": 90,
            "minimum_calendar_dte": 72,
            "maximum_calendar_dte": 115,
            "long_put_target_delta": 0.20,
        },
        {
            "structure": "protective_put",
            "target_calendar_dte": 90,
            "minimum_calendar_dte": 72,
            "maximum_calendar_dte": 115,
            "long_put_target_delta": 0.30,
        },
        {
            "structure": "put_spread",
            "target_calendar_dte": 90,
            "minimum_calendar_dte": 72,
            "maximum_calendar_dte": 115,
            "long_put_target_delta": 0.25,
            "short_put_target_delta": 0.10,
        },
        {
            "structure": "protective_put",
            "target_calendar_dte": 180,
            "minimum_calendar_dte": 145,
            "maximum_calendar_dte": 225,
            "long_put_target_delta": 0.20,
        },
    ]
    specs: list[dict[str, Any]] = []
    for structure in structures:
        for budget in budgets:
            spec = dict(_insurance_base())
            spec.update(structure)
            spec["maximum_net_debit_nav"] = budget
            spec["annual_gross_debit_nav"] = budget
            specs.append(spec)
    return specs


def _slice_metrics(
    frame: pd.DataFrame,
    config: dict[str, Any],
    start: str,
    end: str,
) -> dict[str, Any]:
    section = frame.loc[start:end].copy()
    return performance_metrics(section, config)


def _normalized_debit_stats(frame: pd.DataFrame) -> tuple[float, float]:
    previous_equity = frame["equity"].shift(1).combine_first(frame["equity"])
    net_entry_debit = (
        frame["insurance_long_premium_paid"]
        - frame["insurance_short_premium_collected"]
    ).clip(lower=0.0)
    daily_rate = net_entry_debit / previous_equity.replace(0.0, np.nan)
    years = max(
        (frame.index[-1] - frame.index[0]).days / 365.25,
        len(frame) / 252.0,
    )
    annualized = float(daily_rate.sum() / years)
    yearly = daily_rate.groupby(frame.index.year).sum()
    return annualized, float(yearly.max()) if len(yearly) else 0.0


def _stress_map(frame: pd.DataFrame) -> dict[str, float]:
    stress = stress_period_metrics(frame)
    return dict(zip(stress["period"], stress["max_drawdown"]))


def _pareto_frontier(rows: pd.DataFrame) -> pd.DataFrame:
    ordered = rows.sort_values(
        ["actual_annual_debit_rate", "max_drawdown_improvement"],
        ascending=[True, False],
    )
    keep: list[int] = []
    best_improvement = -np.inf
    for idx, row in ordered.iterrows():
        improvement = float(row["max_drawdown_improvement"])
        if improvement > best_improvement + 1e-12:
            keep.append(idx)
            best_improvement = improvement
    return rows.loc[keep].sort_values("actual_annual_debit_rate")


def main() -> None:
    config = _load_config()
    output_dir = PROJECT_ROOT / "outputs" / "ten_year_v0_3_insurance"
    output_dir.mkdir(parents=True, exist_ok=True)

    data, metadata = build_research_dataset(config, PROJECT_ROOT)
    scores, components = build_scores(data, config)
    states = run_state_machine(scores, config)
    split = config["capital_splits"]["split_35_20_45"]
    modules_off = {
        "covered_call": False,
        "insurance": False,
        "cash_secured_put": False,
    }
    baseline, _, _ = run_options_proxy_backtest(
        data,
        states,
        "QQQ",
        split,
        config,
        modules=modules_off,
        strategy_name="QQQ_PS_NO_INSURANCE",
    )
    baseline_metrics = performance_metrics(baseline, config)
    baseline_first = _slice_metrics(
        baseline, config, "2016-07-25", "2020-12-31"
    )
    baseline_second = _slice_metrics(
        baseline, config, "2021-01-01", "2026-07-24"
    )
    baseline_stress = _stress_map(baseline)

    rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    for candidate_id, insurance in enumerate(_candidate_specs(), start=1):
        name = f"I{candidate_id:03d}"
        frame, trades, summary = run_options_proxy_backtest(
            data,
            states,
            "QQQ",
            split,
            config,
            modules={
                "covered_call": False,
                "insurance": True,
                "cash_secured_put": False,
            },
            option_overrides={"insurance": insurance},
            strategy_name=name,
        )
        metrics = performance_metrics(frame, config)
        first = _slice_metrics(
            frame, config, "2016-07-25", "2020-12-31"
        )
        second = _slice_metrics(
            frame, config, "2021-01-01", "2026-07-24"
        )
        stress = _stress_map(frame)
        annual_debit, max_year_debit = _normalized_debit_stats(frame)
        stress_improvements = {
            period: stress[period] - baseline_stress[period]
            for period in baseline_stress
        }
        row = {
            "candidate_id": name,
            "structure": insurance["structure"],
            "target_dte": insurance["target_calendar_dte"],
            "long_put_delta": insurance["long_put_target_delta"],
            "short_put_delta": insurance.get("short_put_target_delta", np.nan),
            "annual_budget_cap": insurance["annual_gross_debit_nav"],
            "single_entry_cap": insurance["maximum_net_debit_nav"],
            "actual_annual_debit_rate": annual_debit,
            "maximum_single_year_debit_rate": max_year_debit,
            "cagr": metrics["cagr"],
            "cagr_change": metrics["cagr"] - baseline_metrics["cagr"],
            "sharpe": metrics["sharpe_excess_bil"],
            "sharpe_change": (
                metrics["sharpe_excess_bil"]
                - baseline_metrics["sharpe_excess_bil"]
            ),
            "max_drawdown": metrics["max_drawdown"],
            "max_drawdown_improvement": (
                metrics["max_drawdown"]
                - baseline_metrics["max_drawdown"]
            ),
            "first_half_mdd_improvement": (
                first["max_drawdown"] - baseline_first["max_drawdown"]
            ),
            "second_half_mdd_improvement": (
                second["max_drawdown"] - baseline_second["max_drawdown"]
            ),
            "insurance_net_pnl": summary["insurance_net_pnl"],
            "insurance_net_entry_debit": summary[
                "insurance_net_entry_debit"
            ],
            "insurance_pnl_during_panic": summary[
                "insurance_pnl_during_panic"
            ],
            "insurance_entries": summary["insurance_entry_groups"],
        }
        for period, improvement in stress_improvements.items():
            row[f"{period}_mdd_improvement"] = improvement
        row["average_stress_mdd_improvement"] = float(
            np.mean(list(stress_improvements.values()))
        )
        row["worst_stress_mdd_improvement"] = float(
            np.min(list(stress_improvements.values()))
        )
        row["mdd_improvement_per_1pct_annual_debit"] = (
            row["max_drawdown_improvement"] / annual_debit * 0.01
            if annual_debit > 0.0
            else np.nan
        )
        rows.append(row)
        if not trades.empty:
            candidate_trades = trades.copy()
            candidate_trades["candidate_id"] = name
            trade_frames.append(candidate_trades)
        print(
            f"{name} {insurance['structure']} "
            f"{insurance['target_calendar_dte']}D "
            f"budget={insurance['annual_gross_debit_nav']:.3%} "
            f"actual={annual_debit:.3%} "
            f"MDD_improvement={row['max_drawdown_improvement']:.3%}",
            flush=True,
        )

    grid = pd.DataFrame(rows).sort_values(
        ["actual_annual_debit_rate", "max_drawdown_improvement"],
        ascending=[True, False],
    )
    pareto = _pareto_frontier(grid)
    trades = (
        pd.concat(trade_frames, ignore_index=True)
        if trade_frames
        else pd.DataFrame()
    )
    score_history = states[
        [
            "state",
            "event_active",
            "P",
            "S",
            "G",
            "E",
            "R_proxy",
            "I",
            "I_need",
            "I_affordability",
        ]
    ].loc[config["strategy"]["evaluation_start"] : config["strategy"]["evaluation_end"]]

    grid.to_csv(output_dir / "insurance_cost_grid.csv", index=False)
    pareto.to_csv(output_dir / "insurance_pareto_frontier.csv", index=False)
    trades.to_csv(output_dir / "insurance_candidate_trades.csv", index=False)
    score_history.to_csv(output_dir / "insurance_score_history.csv")
    manifest = {
        "model": "I insurance model v0.3",
        "research_only": True,
        "historical_option_chain_used": False,
        "evaluation_start": config["strategy"]["evaluation_start"],
        "evaluation_end": config["strategy"]["evaluation_end"],
        "trading_days": int(len(baseline)),
        "candidate_count": int(len(grid)),
        "baseline": baseline_metrics,
        "data_metadata": metadata,
        "selection_note": (
            "No candidate is promoted automatically. Select a robust cost "
            "plateau using cost, full-period drawdown, both subperiods, and "
            "all three named stress windows."
        ),
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
