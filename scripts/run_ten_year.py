#!/usr/bin/env python3
from __future__ import annotations

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
    account_risk_translation,
    build_event_log,
    performance_metrics,
    run_fixed_weight_backtest,
    run_strategy_backtest,
    stress_period_metrics,
)
from haven.data import build_research_dataset  # noqa: E402
from haven.model import build_scores, run_state_machine  # noqa: E402
from haven.reporting import (  # noqa: E402
    create_equity_chart,
    create_signal_chart,
    write_report,
)


def _load_config() -> dict:
    path = PROJECT_ROOT / "config" / "haven_v0_1.yaml"
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
    ]
    coverage = evaluation[required].notna().mean()
    failed = coverage[coverage < 0.98]
    if not failed.empty:
        raise RuntimeError(f"核心数据覆盖率不足：{failed.to_dict()}")


def main() -> None:
    config = _load_config()
    output_dir = PROJECT_ROOT / "outputs" / "ten_year_v0_1"
    output_dir.mkdir(parents=True, exist_ok=True)

    data, metadata = build_research_dataset(config, PROJECT_ROOT)
    _validate_dataset(data, config)
    scores, components = build_scores(data, config)
    states = run_state_machine(scores, config)

    results: dict[str, pd.DataFrame] = {}
    splits = config["capital_splits"]
    for split_name, split in splits.items():
        suffix = split_name.replace("split_", "")
        for asset in ["QQQ", "TQQQ"]:
            name = f"{asset}_Haven_{suffix}"
            results[name] = run_strategy_backtest(
                data, states, asset, split, config
            )

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

    metric_rows = []
    for name, result in results.items():
        row = {"strategy": name}
        row.update(performance_metrics(result, config))
        metric_rows.append(row)
    metrics = pd.DataFrame(metric_rows).sort_values("strategy")

    stress_frames = []
    for name in [
        "QQQ_Haven_35_20_45",
        "QQQ_Haven_50_15_35",
        "QQQ_Haven_60_10_30",
        "QQQ_BuyHold",
        "TQQQ_Haven_35_20_45",
        "TQQQ_BuyHold",
    ]:
        stress = stress_period_metrics(results[name])
        stress.insert(0, "strategy", name)
        stress_frames.append(stress)
    stress_table = pd.concat(stress_frames, ignore_index=True)

    event_log = build_event_log(states, data, config)
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]
    state_counts = (
        states.loc[start:end, "state"]
        .value_counts()
        .rename_axis("state")
        .reset_index(name="trading_days")
    )
    state_counts["share"] = (
        state_counts["trading_days"] / state_counts["trading_days"].sum()
    )

    sensitivity_rows = []
    base_split = splits["split_35_20_45"]
    for panic_trigger in config["backtest"]["sensitivity_panic_triggers"]:
        for stabilization_1 in config["backtest"][
            "sensitivity_stabilization_1"
        ]:
            altered_states = run_state_machine(
                scores,
                config,
                {
                    "panic_trigger": float(panic_trigger),
                    "stabilization_1": float(stabilization_1),
                },
            )
            result = run_strategy_backtest(
                data, altered_states, "QQQ", base_split, config
            )
            metric = performance_metrics(result, config)
            sensitivity_rows.append(
                {
                    "panic_trigger": float(panic_trigger),
                    "stabilization_1": float(stabilization_1),
                    "total_return": metric["total_return"],
                    "cagr": metric["cagr"],
                    "sharpe_excess_bil": metric["sharpe_excess_bil"],
                    "max_drawdown": metric["max_drawdown"],
                    "average_asset_weight": metric["average_asset_weight"],
                }
            )
    sensitivity = pd.DataFrame(sensitivity_rows)
    risk_translation = account_risk_translation(splits, config)

    metrics.to_csv(output_dir / "metrics.csv", index=False)
    stress_table.to_csv(output_dir / "stress_periods.csv", index=False)
    event_log.to_csv(output_dir / "panic_events.csv", index=False)
    state_counts.to_csv(output_dir / "state_counts.csv", index=False)
    sensitivity.to_csv(output_dir / "sensitivity.csv", index=False)
    risk_translation.to_csv(
        output_dir / "account_risk_translation.csv", index=False
    )
    data.loc[start:end].to_csv(output_dir / "research_dataset.csv")
    scores.loc[start:end].to_csv(output_dir / "score_history.csv")
    states.loc[start:end].to_csv(output_dir / "state_history.csv")
    for component_name, frame in components.items():
        frame.loc[start:end].to_csv(
            output_dir / f"score_components_{component_name}.csv"
        )
    equity_curves = pd.DataFrame(
        {
            name: frame["equity"] / frame["equity"].iloc[0] * 100.0
            for name, frame in results.items()
        }
    )
    equity_curves.to_csv(output_dir / "equity_curves.csv")
    results["QQQ_Haven_35_20_45"].to_csv(
        output_dir / "daily_primary_qqq.csv"
    )
    results["TQQQ_Haven_35_20_45"].to_csv(
        output_dir / "daily_primary_tqqq.csv"
    )

    metadata["model_constraints"] = {
        "lookahead": "close signal at t, weight effective at t+1",
        "options_pnl_included": False,
        "premium_proxy_max_coverage": 0.70,
        "transaction_cost_bps": config["backtest"]["transaction_cost_bps"],
        "cash_asset": "BIL-like DGS3MO cash proxy, lagged one day",
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    create_equity_chart(
        results,
        [
            "QQQ_Haven_35_20_45",
            "QQQ_Haven_50_15_35",
            "QQQ_Haven_60_10_30",
            "QQQ_BuyHold",
            "BIL_like_Cash",
        ],
        output_dir / "qqq_equity_drawdown.png",
        "QQQ: Haven allocations versus buy-and-hold",
    )
    create_equity_chart(
        results,
        [
            "TQQQ_Haven_35_20_45",
            "TQQQ_Haven_50_15_35",
            "TQQQ_Haven_60_10_30",
            "TQQQ_BuyHold",
            "BIL_like_Cash",
        ],
        output_dir / "tqqq_equity_drawdown.png",
        "TQQQ: Haven allocations versus buy-and-hold",
    )
    create_signal_chart(
        scores,
        states,
        results["QQQ_Haven_35_20_45"],
        start,
        end,
        output_dir / "scores_states_weights.png",
    )
    report_path = write_report(
        output_dir,
        metrics,
        stress_table,
        event_log,
        sensitivity,
        state_counts,
        risk_translation,
        metadata,
    )
    print(f"REPORT={report_path}")
    print(metrics[
        [
            "strategy",
            "total_return",
            "cagr",
            "sharpe_excess_bil",
            "max_drawdown",
        ]
    ].to_string(index=False))


if __name__ == "__main__":
    main()
