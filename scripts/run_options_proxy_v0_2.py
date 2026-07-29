#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from haven.backtest import (  # noqa: E402
    performance_metrics,
    run_fixed_weight_backtest,
    run_strategy_backtest,
    stress_period_metrics,
)
from haven.data import build_research_dataset  # noqa: E402
from haven.model import build_scores, run_state_machine  # noqa: E402
from haven.options_proxy import run_options_proxy_backtest  # noqa: E402
from haven.reporting_v0_2 import (  # noqa: E402
    create_equity_drawdown_chart,
    create_greed_call_chart,
    create_option_attribution_chart,
    write_options_proxy_report,
)


def _load_config() -> dict:
    path = PROJECT_ROOT / "config" / "haven_v0_2_options_proxy.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _validate_dataset(data: pd.DataFrame, config: dict) -> None:
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]
    evaluation = data.loc[start:end]
    if len(evaluation) < 2500:
        raise RuntimeError(f"十年样本交易日不足：{len(evaluation)}")
    required = [
        "qqq_close",
        "tqqq_close",
        "bil_close",
        "ndx_close",
        "vxn_close",
        "vix_close",
        "vix3m_close",
        "credit_proxy",
        "cash_total_return",
        "cash_yield_pct",
    ]
    coverage = evaluation[required].notna().mean()
    failed = coverage[coverage < 0.98]
    if not failed.empty:
        raise RuntimeError(f"核心数据覆盖率不足：{failed.to_dict()}")


def main() -> None:
    config = _load_config()
    output_dir = PROJECT_ROOT / "outputs" / "ten_year_v0_2_options_proxy"
    output_dir.mkdir(parents=True, exist_ok=True)

    data, metadata = build_research_dataset(config, PROJECT_ROOT)
    _validate_dataset(data, config)
    scores, components = build_scores(data, config)
    states = run_state_machine(scores, config)
    splits = config["capital_splits"]
    primary_split = splits["split_35_20_45"]

    results: dict[str, pd.DataFrame] = {}
    trade_frames: list[pd.DataFrame] = []
    option_summaries: list[dict] = []

    results["QQQ_PS_Only"] = run_strategy_backtest(
        data, states, "QQQ", primary_split, config
    )
    results["TQQQ_PS_Only"] = run_strategy_backtest(
        data, states, "TQQQ", primary_split, config
    )

    proxy_jobs = [
        (
            "QQQ_PS_Insurance",
            "QQQ",
            primary_split,
            {
                "covered_call": False,
                "insurance": True,
                "cash_secured_put": False,
            },
        ),
        (
            "QQQ_PS_GreedCall",
            "QQQ",
            primary_split,
            {
                "covered_call": True,
                "insurance": False,
                "cash_secured_put": False,
            },
        ),
        (
            "QQQ_PS_Premium",
            "QQQ",
            primary_split,
            {
                "covered_call": True,
                "insurance": False,
                "cash_secured_put": True,
            },
        ),
        (
            "QQQ_Full_35_20_45",
            "QQQ",
            primary_split,
            {
                "covered_call": True,
                "insurance": True,
                "cash_secured_put": True,
            },
        ),
        (
            "QQQ_Full_50_15_35",
            "QQQ",
            splits["split_50_15_35"],
            {
                "covered_call": True,
                "insurance": True,
                "cash_secured_put": True,
            },
        ),
        (
            "QQQ_Full_60_10_30",
            "QQQ",
            splits["split_60_10_30"],
            {
                "covered_call": True,
                "insurance": True,
                "cash_secured_put": True,
            },
        ),
        (
            "TQQQ_PS_Insurance",
            "TQQQ",
            primary_split,
            {
                "covered_call": False,
                "insurance": True,
                "cash_secured_put": False,
            },
        ),
        (
            "TQQQ_PS_GreedCall",
            "TQQQ",
            primary_split,
            {
                "covered_call": True,
                "insurance": False,
                "cash_secured_put": False,
            },
        ),
        (
            "TQQQ_Full_35_20_45",
            "TQQQ",
            primary_split,
            {
                "covered_call": True,
                "insurance": True,
                "cash_secured_put": True,
            },
        ),
    ]
    for name, asset, split, modules in proxy_jobs:
        frame, trades, summary = run_options_proxy_backtest(
            data,
            states,
            asset,
            split,
            config,
            modules=modules,
            strategy_name=name,
        )
        results[name] = frame
        option_summaries.append(summary)
        if not trades.empty:
            trade_frames.append(trades)

    results["QQQ_Static35"] = run_fixed_weight_backtest(
        data, "QQQ", 0.35, config
    )
    results["TQQQ_Static35"] = run_fixed_weight_backtest(
        data, "TQQQ", 0.35, config
    )
    results["QQQ_BuyHold"] = run_fixed_weight_backtest(
        data, "QQQ", 1.0, config
    )
    results["TQQQ_BuyHold"] = run_fixed_weight_backtest(
        data, "TQQQ", 1.0, config
    )
    results["BIL_like_Cash"] = run_fixed_weight_backtest(
        data, "QQQ", 0.0, config
    )

    metric_rows: list[dict] = []
    for name, frame in results.items():
        row = {"strategy": name}
        row.update(performance_metrics(frame, config))
        metric_rows.append(row)
    metrics = pd.DataFrame(metric_rows).sort_values("strategy")
    option_summary = pd.DataFrame(option_summaries).sort_values("strategy")
    option_trades = (
        pd.concat(trade_frames, ignore_index=True)
        if trade_frames
        else pd.DataFrame()
    )
    diagnostic_open = option_trades[
        (option_trades["strategy_name"] == "QQQ_Full_35_20_45")
        & (option_trades["action"] == "OPEN")
    ].copy()
    diagnostic_open["dte"] = (
        diagnostic_open["expiry"] - diagnostic_open["date"]
    ).dt.days
    diagnostic_open["moneyness"] = (
        diagnostic_open["strike"] / diagnostic_open["spot"] - 1.0
    )
    diagnostic_open["premium_to_spot"] = (
        diagnostic_open["execution_price"] / diagnostic_open["spot"]
    )
    diagnostic_open["leg"] = diagnostic_open["strategy_type"]
    diagnostic_open.loc[
        diagnostic_open["strategy_type"].eq("insurance")
        & diagnostic_open["side"].gt(0),
        "leg",
    ] = "insurance_long_put"
    diagnostic_open.loc[
        diagnostic_open["strategy_type"].eq("insurance")
        & diagnostic_open["side"].lt(0),
        "leg",
    ] = "insurance_short_put"
    trade_diagnostics = (
        diagnostic_open.groupby("leg", as_index=False)
        .agg(
            entries=("group_id", "count"),
            median_dte=("dte", "median"),
            median_moneyness=("moneyness", "median"),
            median_premium_to_spot=("premium_to_spot", "median"),
            median_iv=("iv", "median"),
            median_abs_delta=("delta", lambda value: value.abs().median()),
            median_contracts=("contracts", "median"),
        )
        .sort_values("leg")
    )

    stress_frames: list[pd.DataFrame] = []
    for name in [
        "QQQ_PS_Only",
        "QQQ_PS_Insurance",
        "QQQ_PS_GreedCall",
        "QQQ_PS_Premium",
        "QQQ_Full_35_20_45",
        "QQQ_BuyHold",
        "TQQQ_PS_Only",
        "TQQQ_Full_35_20_45",
        "TQQQ_BuyHold",
    ]:
        stress = stress_period_metrics(results[name])
        stress.insert(0, "strategy", name)
        stress_frames.append(stress)
    stress_table = pd.concat(stress_frames, ignore_index=True)

    sensitivity_rows: list[dict] = []
    for iv_multiplier in [0.90, 1.00, 1.10]:
        for spread_multiplier in [0.75, 1.00, 1.50]:
            scenario = (
                f"IVx{iv_multiplier:.2f}_Spreadx{spread_multiplier:.2f}"
            )
            frame, _, summary = run_options_proxy_backtest(
                data,
                states,
                "QQQ",
                primary_split,
                config,
                option_overrides={
                    "iv_multiplier": iv_multiplier,
                    "spread_multiplier": spread_multiplier,
                },
                strategy_name=scenario,
            )
            metric = performance_metrics(frame, config)
            sensitivity_rows.append(
                {
                    "scenario": scenario,
                    "iv_multiplier": iv_multiplier,
                    "spread_multiplier": spread_multiplier,
                    "cagr": metric["cagr"],
                    "sharpe_excess_bil": metric["sharpe_excess_bil"],
                    "max_drawdown": metric["max_drawdown"],
                    "option_net_pnl": (
                        summary["covered_call_net_pnl"]
                        + summary["cash_secured_put_net_pnl"]
                        + summary["insurance_net_pnl"]
                        + summary["assigned_inventory_net_pnl"]
                    ),
                }
            )
    sensitivity = pd.DataFrame(sensitivity_rows)

    nav_rows: list[dict] = []
    for reference_nav in [100000.0, 250000.0, 500000.0, 1000000.0]:
        nav_config = copy.deepcopy(config)
        nav_config["strategy"]["initial_nav"] = reference_nav
        frame, _, summary = run_options_proxy_backtest(
            data,
            states,
            "QQQ",
            primary_split,
            nav_config,
            strategy_name=f"NAV_{int(reference_nav)}",
        )
        metric = performance_metrics(frame, nav_config)
        option_net_pnl = (
            summary["covered_call_net_pnl"]
            + summary["cash_secured_put_net_pnl"]
            + summary["insurance_net_pnl"]
            + summary["assigned_inventory_net_pnl"]
        )
        nav_rows.append(
            {
                "reference_nav": reference_nav,
                "cagr": metric["cagr"],
                "sharpe_excess_bil": metric["sharpe_excess_bil"],
                "max_drawdown": metric["max_drawdown"],
                "call_entries": summary["covered_call_entry_groups"],
                "csp_entries": summary["csp_entry_groups"],
                "insurance_entries": summary["insurance_entry_groups"],
                "option_net_pnl_pct_initial": option_net_pnl / reference_nav,
            }
        )
    nav_sensitivity = pd.DataFrame(nav_rows)

    structure_rows: list[dict] = []
    for structure in ["put_spread", "protective_put"]:
        frame, _, summary = run_options_proxy_backtest(
            data,
            states,
            "QQQ",
            primary_split,
            config,
            option_overrides={"insurance": {"structure": structure}},
            strategy_name=f"Insurance_{structure}",
        )
        metric = performance_metrics(frame, config)
        structure_rows.append(
            {
                "insurance_structure": structure,
                "cagr": metric["cagr"],
                "sharpe_excess_bil": metric["sharpe_excess_bil"],
                "max_drawdown": metric["max_drawdown"],
                "insurance_net_pnl": summary["insurance_net_pnl"],
                "insurance_pnl_during_panic": summary[
                    "insurance_pnl_during_panic"
                ],
                "insurance_net_entry_debit": summary[
                    "insurance_net_entry_debit"
                ],
            }
        )
    structure_sensitivity = pd.DataFrame(structure_rows)

    metrics.to_csv(output_dir / "metrics.csv", index=False)
    option_summary.to_csv(output_dir / "option_summary.csv", index=False)
    option_trades.to_csv(output_dir / "option_trades.csv", index=False)
    trade_diagnostics.to_csv(
        output_dir / "option_trade_diagnostics.csv", index=False
    )
    stress_table.to_csv(output_dir / "stress_periods.csv", index=False)
    sensitivity.to_csv(output_dir / "proxy_sensitivity.csv", index=False)
    nav_sensitivity.to_csv(
        output_dir / "contract_granularity_sensitivity.csv", index=False
    )
    structure_sensitivity.to_csv(
        output_dir / "insurance_structure_sensitivity.csv", index=False
    )
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]
    scores.loc[start:end].to_csv(output_dir / "score_history.csv")
    states.loc[start:end].to_csv(output_dir / "state_history.csv")
    for component_name, component_frame in components.items():
        component_frame.loc[start:end].to_csv(
            output_dir / f"score_components_{component_name}.csv"
        )
    results["QQQ_Full_35_20_45"].to_csv(
        output_dir / "daily_qqq_full.csv"
    )
    results["TQQQ_Full_35_20_45"].to_csv(
        output_dir / "daily_tqqq_full.csv"
    )
    equity_curves = pd.DataFrame(
        {
            name: frame["equity"] / frame["equity"].iloc[0] * 100.0
            for name, frame in results.items()
        }
    )
    equity_curves.to_csv(output_dir / "equity_curves.csv")

    metadata["reference_sleeve_nav"] = float(
        config["strategy"]["initial_nav"]
    )
    metadata["options_proxy"] = {
        "historical_option_chain_used": False,
        "model": config["options_proxy"]["model_label"],
        "pricing": "Black-Scholes daily mark with VXN anchor",
        "signal_delay": "one trading day",
        "contract_multiplier": config["options_proxy"][
            "contract_multiplier"
        ],
        "commission_per_contract_per_leg": config["options_proxy"][
            "commission_per_contract_per_leg"
        ],
        "base_iv_multiplier": config["options_proxy"]["iv_multiplier"],
        "base_spread_multiplier": config["options_proxy"][
            "spread_multiplier"
        ],
        "main_insurance_structure": config["options_proxy"]["insurance"][
            "structure"
        ],
        "early_assignment": (
            "economic close/expiry proxy; exact American early assignment "
            "is not reconstructed"
        ),
    }
    metadata["model_constraints"] = {
        "production_workflow_modified": False,
        "paper_or_live_enabled": False,
        "outputs_latest_modified": False,
        "tqqq_status": "stress research only",
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    create_equity_drawdown_chart(
        results,
        [
            "QQQ_PS_Only",
            "QQQ_PS_Insurance",
            "QQQ_PS_GreedCall",
            "QQQ_PS_Premium",
            "QQQ_Full_35_20_45",
            "QQQ_BuyHold",
        ],
        output_dir / "qqq_options_ablation.png",
        "QQQ Haven: option-proxy module ablation",
    )
    create_equity_drawdown_chart(
        results,
        [
            "TQQQ_PS_Only",
            "TQQQ_PS_Insurance",
            "TQQQ_PS_GreedCall",
            "TQQQ_Full_35_20_45",
            "TQQQ_BuyHold",
        ],
        output_dir / "tqqq_options_ablation.png",
        "TQQQ Haven: option-proxy stress research",
    )
    create_option_attribution_chart(
        results["QQQ_Full_35_20_45"],
        float(config["strategy"]["initial_nav"]),
        output_dir / "option_pnl_attribution.png",
        "QQQ full strategy: option P&L attribution",
    )
    create_greed_call_chart(
        results["QQQ_Full_35_20_45"],
        output_dir / "greed_call_activation.png",
    )

    report_path = write_options_proxy_report(
        output_dir,
        metrics,
        option_summary,
        trade_diagnostics,
        stress_table,
        sensitivity,
        nav_sensitivity,
        structure_sensitivity,
        metadata,
    )
    print(f"REPORT={report_path}")
    print(
        metrics[
            metrics["strategy"].isin(
                [
                    "QQQ_PS_Only",
                    "QQQ_PS_Insurance",
                    "QQQ_PS_GreedCall",
                    "QQQ_PS_Premium",
                    "QQQ_Full_35_20_45",
                    "TQQQ_PS_Only",
                    "TQQQ_Full_35_20_45",
                ]
            )
        ][
            [
                "strategy",
                "total_return",
                "cagr",
                "sharpe_excess_bil",
                "max_drawdown",
            ]
        ].to_string(index=False)
    )
    print(
        option_summary[
            option_summary["strategy"].isin(
                ["QQQ_Full_35_20_45", "TQQQ_Full_35_20_45"]
            )
        ][
            [
                "strategy",
                "covered_call_net_pnl",
                "cash_secured_put_net_pnl",
                "insurance_net_pnl",
                "assigned_inventory_net_pnl",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
