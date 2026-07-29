#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

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


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def main() -> None:
    base = yaml.safe_load(
        (
            PROJECT_ROOT / "config" / "haven_v0_2_options_proxy.yaml"
        ).read_text(encoding="utf-8")
    )
    overlay = yaml.safe_load(
        (
            PROJECT_ROOT / "config" / "haven_v0_3_insurance_model.yaml"
        ).read_text(encoding="utf-8")
    )
    config = _merge(base, {"options_proxy": overlay["options_proxy"]})
    output_dir = PROJECT_ROOT / "outputs" / "ten_year_v0_3_insurance"
    output_dir.mkdir(parents=True, exist_ok=True)

    data, _ = build_research_dataset(config, PROJECT_ROOT)
    scores, _ = build_scores(data, config)
    states = run_state_machine(scores, config)
    split = config["capital_splits"]["split_35_20_45"]

    jobs = {
        "QQQ_PS_Only": {
            "covered_call": False,
            "insurance": False,
            "cash_secured_put": False,
        },
        "QQQ_v0_3_Insurance": {
            "covered_call": False,
            "insurance": True,
            "cash_secured_put": False,
        },
        "QQQ_v0_3_Full": {
            "covered_call": True,
            "insurance": True,
            "cash_secured_put": True,
        },
    }
    metric_rows: list[dict[str, Any]] = []
    stress_rows: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for name, modules in jobs.items():
        frame, trades, summary = run_options_proxy_backtest(
            data,
            states,
            "QQQ",
            split,
            config,
            modules=modules,
            strategy_name=name,
        )
        row = {"strategy": name}
        row.update(performance_metrics(frame, config))
        metric_rows.append(row)
        stress = stress_period_metrics(frame)
        stress.insert(0, "strategy", name)
        stress_rows.append(stress)
        summaries.append(summary)
        if name == "QQQ_v0_3_Full":
            frame.to_csv(output_dir / "recommended_full_daily.csv")
            trades.to_csv(
                output_dir / "recommended_full_option_trades.csv",
                index=False,
            )

    pd.DataFrame(metric_rows).to_csv(
        output_dir / "recommended_metrics.csv", index=False
    )
    pd.concat(stress_rows, ignore_index=True).to_csv(
        output_dir / "recommended_stress_periods.csv", index=False
    )
    pd.DataFrame(summaries).to_csv(
        output_dir / "recommended_option_summary.csv", index=False
    )
    manifest = {
        "model": "I insurance model v0.3",
        "base_config": "haven_v0_2_options_proxy.yaml",
        "overlay_config": "haven_v0_3_insurance_model.yaml",
        "historical_option_chain_used": False,
        "paper_or_live_enabled": False,
        "trading_days": 2514,
    }
    (output_dir / "recommended_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
