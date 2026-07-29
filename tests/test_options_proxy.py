from __future__ import annotations

import copy
import math
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

from haven.options_proxy import (  # noqa: E402
    _call_terms,
    _csp_terms,
    black_scholes_price_delta,
    build_option_proxy_inputs,
    choose_expiry,
)


class HavenOptionProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(
            (
                PROJECT_ROOT
                / "config"
                / "haven_v0_2_options_proxy.yaml"
            ).read_text(encoding="utf-8")
        )

    def test_black_scholes_put_call_parity(self) -> None:
        spot = 100.0
        strike = 105.0
        years = 45.0 / 365.0
        rate = 0.04
        dividend = 0.008
        volatility = 0.24
        call, _ = black_scholes_price_delta(
            spot, strike, years, rate, dividend, volatility, "call"
        )
        put, _ = black_scholes_price_delta(
            spot, strike, years, rate, dividend, volatility, "put"
        )
        parity = (
            spot * math.exp(-dividend * years)
            - strike * math.exp(-rate * years)
        )
        self.assertAlmostEqual(call - put, parity, places=8)

    def test_expiry_payoff_and_delta(self) -> None:
        call, call_delta = black_scholes_price_delta(
            120.0, 100.0, 0.0, 0.04, 0.0, 0.20, "call"
        )
        put, put_delta = black_scholes_price_delta(
            80.0, 100.0, 0.0, 0.04, 0.0, 0.20, "put"
        )
        self.assertEqual(call, 20.0)
        self.assertEqual(put, 20.0)
        self.assertEqual(call_delta, 1.0)
        self.assertEqual(put_delta, -1.0)

    def test_monthly_expiry_respects_dte_window(self) -> None:
        index = pd.bdate_range("2024-01-02", "2024-06-28")
        date = pd.Timestamp("2024-01-10")
        expiry = choose_expiry(date, index, 35, 28, 50)
        self.assertIsNotNone(expiry)
        dte = int((expiry - date).days)
        self.assertGreaterEqual(dte, 28)
        self.assertLessEqual(dte, 50)
        self.assertEqual(expiry.weekday(), 4)

    def test_iv_input_has_no_future_dependency(self) -> None:
        index = pd.bdate_range("2020-01-02", periods=180)
        base = pd.DataFrame(index=index)
        returns = pd.Series(
            np.sin(np.arange(len(index)) / 9.0) * 0.005,
            index=index,
        )
        changed = returns.copy()
        changed.iloc[130:] = changed.iloc[130:] * -20.0
        for frame, series in [(base, returns)]:
            frame["qqq_price_return"] = series
            frame["qqq_close"] = 100.0 * (1.0 + series).cumprod()
            frame["qqq_total_return"] = series
            frame["qqq_dividend"] = 0.0
            frame["vxn_close"] = 22.0
            frame["cash_yield_pct"] = 4.0
        altered = base.copy()
        altered["qqq_price_return"] = changed
        altered["qqq_close"] = 100.0 * (1.0 + changed).cumprod()
        altered["qqq_total_return"] = changed
        first = build_option_proxy_inputs(base, "QQQ", self.config)
        second = build_option_proxy_inputs(altered, "QQQ", self.config)
        pd.testing.assert_series_equal(
            first["atm_iv"].iloc[:130],
            second["atm_iv"].iloc[:130],
            check_names=False,
        )

    def test_greed_call_requires_joint_gate(self) -> None:
        option_config = copy.deepcopy(self.config["options_proxy"])
        decision = pd.Series(
            {
                "state": "GREED_EXHAUSTING",
                "G": 78.0,
                "E": 64.0,
                "R_proxy": 62.0,
            }
        )
        terms = _call_terms(decision, option_config)
        self.assertIsNotNone(terms)
        self.assertAlmostEqual(terms[0], 0.50)
        decision["R_proxy"] = 40.0
        self.assertIsNone(_call_terms(decision, option_config))

    def test_put_and_call_use_separate_premium_scores(self) -> None:
        option_config = copy.deepcopy(self.config["options_proxy"])
        call_decision = pd.Series(
            {
                "state": "GREED_EXHAUSTING",
                "G": 78.0,
                "E": 64.0,
                "R_proxy": 99.0,
                "R_put": 90.0,
                "R_call": 40.0,
            }
        )
        self.assertIsNone(_call_terms(call_decision, option_config))

        put_decision = pd.Series(
            {
                "state": "RANGE_CARRY",
                "R_proxy": 1.0,
                "R_put": 80.0,
                "R_call": 10.0,
                "P": 20.0,
                "E": 30.0,
                "breadth_stress": 20.0,
                "liquidity_stress": 20.0,
            }
        )
        self.assertIsNotNone(_csp_terms(put_decision, option_config))

        option_config["cash_secured_put"][
            "entry_breadth_stress_max"
        ] = 15.0
        self.assertIsNone(_csp_terms(put_decision, option_config))


if __name__ == "__main__":
    unittest.main()
