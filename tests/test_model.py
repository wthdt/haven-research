from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from haven.backtest import run_strategy_backtest
from haven.model import (
    build_insurance_scores,
    rolling_percentile,
    run_state_machine,
)


class HavenModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(
            (PROJECT_ROOT / "config" / "haven_v0_1.yaml").read_text(
                encoding="utf-8"
            )
        )

    def test_rolling_percentile_has_no_future_dependency(self) -> None:
        index = pd.date_range("2020-01-01", periods=500, freq="B")
        original = pd.Series(np.arange(500, dtype=float), index=index)
        changed = original.copy()
        changed.iloc[350:] = changed.iloc[350:] * -100.0
        score_a = rolling_percentile(original, 252, 50)
        score_b = rolling_percentile(changed, 252, 50)
        pd.testing.assert_series_equal(
            score_a.iloc[:350], score_b.iloc[:350], check_names=False
        )

    def test_insurance_score_has_no_future_dependency(self) -> None:
        index = pd.date_range("2020-01-01", periods=120, freq="B")
        panic = pd.Series(
            30.0 + np.sin(np.arange(120) / 5.0) * 15.0,
            index=index,
        )
        greed = pd.Series(55.0, index=index)
        exhaustion = pd.Series(50.0, index=index)
        premium = pd.Series(45.0, index=index)
        changed = panic.copy()
        changed.iloc[90:] = 100.0
        first = build_insurance_scores(
            panic, greed, exhaustion, premium
        )
        second = build_insurance_scores(
            changed, greed, exhaustion, premium
        )
        pd.testing.assert_frame_equal(
            first.iloc[:90],
            second.iloc[:90],
            check_names=False,
        )

    def test_event_requires_trigger_and_deployment_is_cumulative(self) -> None:
        index = pd.date_range("2024-01-02", periods=10, freq="B")
        scores = pd.DataFrame(index=index)
        scores["P"] = [20, 55, 55, 60, 65, 60, 55, 40, 25, 25]
        scores["P_coverage"] = 1.0
        scores["P_price"] = 60.0
        scores["S"] = [20, 20, 20, 40, 60, 20, 20, 75, 75, 75]
        scores["S_coverage"] = 1.0
        scores["G"] = 20.0
        scores["G_coverage"] = 1.0
        scores["E"] = 20.0
        scores["E_coverage"] = 1.0
        scores["R_proxy"] = 50.0
        scores["signal_close"] = np.linspace(100.0, 109.0, len(index))
        scores["ma20"] = 99.0
        scores["range_condition"] = False

        states = run_state_machine(scores, self.config)
        self.assertFalse(states.iloc[1]["event_active"])
        self.assertTrue(states.iloc[2]["event_started"])
        self.assertEqual(states.iloc[2]["state"], "PANIC_ACCELERATING")
        self.assertAlmostEqual(
            states.iloc[3]["deployment_fraction"], 0.15, places=8
        )
        self.assertAlmostEqual(
            states.iloc[4]["deployment_fraction"], 0.40, places=8
        )
        self.assertGreaterEqual(
            states.iloc[5]["deployment_fraction"],
            states.iloc[4]["deployment_fraction"],
        )

    def test_signal_weight_is_applied_one_day_later(self) -> None:
        config = copy.deepcopy(self.config)
        config["strategy"]["evaluation_start"] = "2024-01-02"
        config["strategy"]["evaluation_end"] = "2024-01-08"
        index = pd.date_range("2024-01-02", periods=5, freq="B")
        data = pd.DataFrame(index=index)
        data["qqq_total_return"] = [0.0, 0.0, 0.10, 0.0, 0.0]
        data["cash_total_return"] = 0.0
        states = pd.DataFrame(index=index)
        states["state"] = [
            "NORMAL_PARTICIPATION",
            "PANIC_STABILIZING_2",
            "PANIC_STABILIZING_2",
            "NORMAL_PARTICIPATION",
            "NORMAL_PARTICIPATION",
        ]
        states["event_id"] = [0, 1, 1, 0, 0]
        states["event_active"] = [False, True, True, False, False]
        states["deployment_fraction"] = [0.0, 0.4, 0.4, 0.0, 0.0]
        for column in ["P", "S", "G", "E", "R_proxy"]:
            states[column] = 0.0

        split = config["capital_splits"]["split_35_20_45"]
        frame = run_strategy_backtest(data, states, "QQQ", split, config)
        self.assertAlmostEqual(frame.iloc[1]["executed_asset_weight"], 0.35)
        self.assertAlmostEqual(
            frame.iloc[2]["executed_asset_weight"],
            0.35 + 0.45 * 0.40,
        )
        self.assertAlmostEqual(frame.iloc[2]["gross_return"], 0.053)

    def test_recovery_gate_blocks_unconfirmed_full_deployment(self) -> None:
        config = copy.deepcopy(self.config)
        config["enriched_scores"] = {
            "recovery_gate": {
                "enabled": True,
                "early": {
                    "breadth_recovery_min": 55.0,
                    "liquidity_relief_min": 30.0,
                },
                "retested": {
                    "breadth_recovery_min": 60.0,
                    "liquidity_relief_min": 40.0,
                    "minimum_days_from_low": 5,
                },
            }
        }
        index = pd.date_range("2024-01-02", periods=9, freq="B")
        scores = pd.DataFrame(index=index)
        scores["P"] = [20, 55, 60, 60, 55, 50, 45, 40, 35]
        scores["P_coverage"] = 1.0
        scores["P_price"] = 60.0
        scores["S"] = [20, 20, 20, 40, 60, 75, 75, 75, 75]
        scores["S_coverage"] = 1.0
        scores["G"] = 20.0
        scores["G_coverage"] = 1.0
        scores["E"] = 20.0
        scores["E_coverage"] = 1.0
        scores["R_proxy"] = 50.0
        scores["signal_close"] = np.linspace(100.0, 108.0, len(index))
        scores["ma20"] = 99.0
        scores["range_condition"] = False
        scores["breadth_recovery"] = 90.0
        scores["liquidity_relief"] = 20.0
        scores["tail_stress"] = 30.0

        blocked = run_state_machine(scores, config)
        self.assertNotIn(
            "RECOVERY_RETESTED", blocked["state"].tolist()
        )
        self.assertLessEqual(
            float(blocked["deployment_fraction"].max()), 0.40
        )

        scores["liquidity_relief"] = 80.0
        confirmed = run_state_machine(scores, config)
        self.assertIn(
            "RECOVERY_RETESTED", confirmed["state"].tolist()
        )


if __name__ == "__main__":
    unittest.main()
