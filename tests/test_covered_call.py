from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
import yaml

from src.haven.covered_call import (
    covered_call_gate,
    screen_covered_calls,
)
from src.haven.options_proxy import black_scholes_price_delta


class CoveredCallScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        project_root = Path(__file__).resolve().parents[1]
        cls.option_config = yaml.safe_load(
            (
                project_root
                / "config"
                / "haven_v0_2_options_proxy.yaml"
            ).read_text(encoding="utf-8")
        )["options_proxy"]

    @staticmethod
    def _model(
        *,
        g: float = 55.0,
        e: float = 50.0,
        r_call: float = 65.0,
    ) -> dict:
        return {
            "status": "RANGE_CARRY",
            "scores": {
                "G": g,
                "G_coverage": 1.0,
                "E": e,
                "E_coverage": 1.0,
                "R": r_call,
                "R_call": r_call,
                "R_call_coverage": 1.0,
            },
        }

    @staticmethod
    def _chain() -> pd.DataFrame:
        as_of = pd.Timestamp("2026-07-28")
        expiry = as_of + pd.Timedelta(days=35)
        spot = 60.0
        rate = 0.04
        volatility = 0.60
        rows = []
        for strike, volume, open_interest in [
            (64.0, 200, 1200),
            (66.0, 500, 3500),
            (68.0, 300, 2500),
            (70.0, 100, 1800),
            (72.0, 40, 600),
        ]:
            theoretical, _ = black_scholes_price_delta(
                spot,
                strike,
                35 / 365,
                rate,
                0.0,
                volatility,
                "call",
            )
            rows.append(
                {
                    "symbol": "TQQQ",
                    "as_of": as_of,
                    "spot": spot,
                    "expiry": expiry,
                    "option_type": "call",
                    "strike": strike,
                    "last": theoretical,
                    "bid": max(theoretical - 0.05, 0.01),
                    "ask": theoretical + 0.05,
                    "volume": volume,
                    "open_interest": open_interest,
                }
            )
        return pd.DataFrame(rows)

    def test_low_greed_blocks_call_entry(self) -> None:
        gate = covered_call_gate(
            self._model(g=35.0),
            self.option_config,
            shares=400,
        )
        self.assertEqual(gate["status"], "WAIT")
        self.assertFalse(gate["eligible"])
        self.assertIn("G≥", gate["reason"])

    def test_contract_granularity_blocks_one_hundred_shares(self) -> None:
        gate = covered_call_gate(
            self._model(),
            self.option_config,
            shares=100,
        )
        self.assertEqual(
            gate["status"],
            "CONTRACT_GRANULARITY_GUARD",
        )
        self.assertEqual(gate["maximum_researched_contracts"], 0)

    def test_screen_ranks_real_bid_candidates_but_never_executes(self) -> None:
        result = screen_covered_calls(
            self._chain(),
            model=self._model(),
            option_config=self.option_config,
            shares=400,
            rate=0.04,
            cost_basis=71.0,
            historical_premium=430.0,
        )
        self.assertEqual(result["status"], "CANDIDATES_RESEARCH_ONLY")
        self.assertTrue(result["gate"]["eligible"])
        self.assertEqual(
            result["gate"]["maximum_researched_contracts"],
            1,
        )
        self.assertGreaterEqual(len(result["candidates"]), 1)
        best = result["candidates"][0]
        self.assertGreater(best["seller_net_credit"], 0.0)
        self.assertIn("effective_exit_price", best)
        self.assertIn(
            "profit_at_assignment_vs_cost_with_historical_premium",
            best,
        )
        self.assertFalse(result["automation_allowed"])


if __name__ == "__main__":
    unittest.main()
