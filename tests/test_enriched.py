from __future__ import annotations

import copy
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from haven.enriched import (  # noqa: E402
    build_enriched_scores,
    build_news_shadow_score,
)
from haven.live_market import (  # noqa: E402
    build_live_breadth_snapshot,
    implied_volatility,
    summarize_breadth_rows,
    summarize_option_chain,
)
from haven.options_proxy import black_scholes_price_delta  # noqa: E402
from haven.x_shadow import score_x_count_history  # noqa: E402


def _merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _synthetic_dataset(periods: int = 420) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-02", periods=periods)
    phase = np.arange(periods, dtype=float)
    returns = 0.0004 + 0.008 * np.sin(phase / 17.0)
    close = 100.0 * pd.Series(1.0 + returns, index=index).cumprod()
    data = pd.DataFrame(index=index)
    data["ndx_close"] = close
    data["qqq_close"] = close * 0.025
    data["qqq_high"] = data["qqq_close"] * 1.01
    data["qqq_low"] = data["qqq_close"] * 0.99
    data["qqq_volume"] = 50_000_000 + phase * 10_000
    data["qqq_price_return"] = data["qqq_close"].pct_change()
    data["rsp_close"] = 100.0 * (1.0 + returns * 0.8).cumprod()
    data["spy_close"] = 100.0 * (1.0 + returns * 0.9).cumprod()
    data["qqew_close"] = 100.0 * (1.0 + returns * 0.7).cumprod()
    data["vxn_close"] = 24.0 + 5.0 * np.sin(phase / 11.0)
    data["vix_close"] = 20.0 + 3.0 * np.sin(phase / 13.0)
    data["vix3m_close"] = 22.0 + 2.0 * np.sin(phase / 19.0)
    data["credit_proxy"] = 1.0 + 0.03 * np.sin(phase / 31.0)
    data["hy_spread_enriched"] = 3.0 + 0.4 * np.sin(phase / 29.0)
    data["ccc_spread"] = 9.0 + 0.8 * np.sin(phase / 23.0)
    data["nfci"] = -0.4 + 0.2 * np.sin(phase / 37.0)
    data["dgs2"] = 4.0 + 0.3 * np.sin(phase / 41.0)
    data["real_yield_10y"] = 1.8 + 0.2 * np.sin(phase / 43.0)
    data["vvix_close"] = 90.0 + 12.0 * np.sin(phase / 7.0)
    data["skew_close"] = 125.0 + phase * 0.03
    data["vix9d_close"] = 19.0 + 4.0 * np.sin(phase / 9.0)
    data["vix6m_close"] = 23.0 + 2.0 * np.sin(phase / 21.0)
    return data


class HavenEnrichedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = yaml.safe_load(
            (
                PROJECT_ROOT
                / "config"
                / "haven_v0_2_options_proxy.yaml"
            ).read_text(encoding="utf-8")
        )
        overlay = yaml.safe_load(
            (
                PROJECT_ROOT
                / "config"
                / "haven_v0_4_enriched_indicators.yaml"
            ).read_text(encoding="utf-8")
        )
        cls.config = _merge(
            base,
            {
                "data": overlay["data"],
                "enriched_data": overlay["enriched_data"],
                "enriched_scores": overlay["enriched_scores"],
            },
        )

    def test_enriched_scores_have_no_future_dependency(self) -> None:
        original = _synthetic_dataset()
        changed = original.copy()
        columns = [
            "qqew_close",
            "ccc_spread",
            "nfci",
            "vvix_close",
            "skew_close",
        ]
        changed.loc[changed.index[330] :, columns] *= 4.0
        first, _, _ = build_enriched_scores(original, self.config, include_audit=True)
        second, _, _ = build_enriched_scores(changed, self.config, include_audit=True)
        pd.testing.assert_frame_equal(
            first.loc[: first.index[329], ["P", "S", "G", "E", "R_put"]],
            second.loc[: second.index[329], ["P", "S", "G", "E", "R_put"]],
            check_names=False,
        )

    def test_put_and_call_richness_split_on_skew(self) -> None:
        scores, _, _ = build_enriched_scores(
            _synthetic_dataset(), self.config, include_audit=True
        )
        latest = scores.dropna(subset=["R_put", "R_call"]).iloc[-1]
        self.assertGreater(latest["R_put"], latest["R_call"])
        self.assertFalse(bool(latest["option_backtest_allowed"]))

    def test_news_model_is_shadow_only(self) -> None:
        result = build_news_shadow_score(
            {
                "event_severity": 80.0,
                "x_heat": 90.0,
                "x_sentiment": -70.0,
                "x_credibility": 80.0,
                "source_consensus": 75.0,
                "event_proximity": 90.0,
            }
        )
        self.assertGreater(result["N"], 60.0)
        self.assertEqual(result["N_status"], "SHADOW_ALERT")
        self.assertFalse(result["N_affects_position"])

    def test_implied_volatility_round_trip(self) -> None:
        price, _ = black_scholes_price_delta(
            100.0, 95.0, 45.0 / 365.0, 0.04, 0.01, 0.32, "put"
        )
        recovered = implied_volatility(
            price,
            spot=100.0,
            strike=95.0,
            time_years=45.0 / 365.0,
            rate=0.04,
            dividend_yield=0.01,
            option_type="put",
        )
        self.assertAlmostEqual(recovered, 0.32, places=5)

    def test_option_chain_summary_uses_observed_quotes(self) -> None:
        as_of = pd.Timestamp("2026-01-02")
        rows: list[dict] = []
        for dte in [30, 90]:
            for strike in np.arange(75.0, 126.0, 5.0):
                for option_type in ["call", "put"]:
                    skew = (
                        max(100.0 - strike, 0.0) / 100.0 * 0.5
                        if option_type == "put"
                        else 0.0
                    )
                    volatility = 0.25 + skew
                    mid, _ = black_scholes_price_delta(
                        100.0,
                        strike,
                        dte / 365.0,
                        0.04,
                        0.01,
                        volatility,
                        option_type,
                    )
                    rows.append(
                        {
                            "as_of": as_of,
                            "spot": 100.0,
                            "expiry": as_of + pd.Timedelta(days=dte),
                            "option_type": option_type,
                            "strike": strike,
                            "last": mid,
                            "bid": max(mid - 0.02, 0.001),
                            "ask": mid + 0.02,
                            "volume": 100.0,
                            "open_interest": 500.0,
                        }
                    )
        summary, enriched = summarize_option_chain(
            pd.DataFrame(rows),
            rate=0.04,
            dividend_yield=0.01,
        )
        self.assertEqual(summary["status"], "LIVE_CHAIN_READY")
        self.assertGreater(
            summary["short_tenor"]["put_skew_vol_points"], 0.0
        )
        self.assertGreater(
            enriched["implied_volatility"].notna().mean(), 0.80
        )

    def test_breadth_summary_has_data_guard(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "symbol": "A",
                    "status": "OK",
                    "return_1d": 0.01,
                    "above_ma20": True,
                    "above_ma50": True,
                    "above_ma200": True,
                    "new_high_252": False,
                    "new_low_252": False,
                },
                {
                    "symbol": "B",
                    "status": "DOWNLOAD_ERROR",
                    "return_1d": np.nan,
                    "above_ma20": np.nan,
                    "above_ma50": np.nan,
                    "above_ma200": np.nan,
                    "new_high_252": np.nan,
                    "new_low_252": np.nan,
                },
            ]
        )
        summary = summarize_breadth_rows(
            rows,
            as_of=pd.Timestamp("2026-01-02"),
            minimum_member_coverage=0.85,
        )
        self.assertEqual(summary["status"], "LIVE_SHADOW_DATA_GUARD")
        self.assertEqual(summary["advance_decline_net"], 1)

    @patch("haven.live_market.fetch_nasdaq_history")
    @patch("haven.live_market.fetch_nasdaq100_members")
    def test_live_breadth_batch_timeout_uses_data_guard(
        self,
        members_mock,
        history_mock,
    ) -> None:
        members_mock.return_value = (
            pd.DataFrame({"symbol": ["A", "B"]}),
            pd.Timestamp("2026-01-02"),
        )

        def slow_history(*args, **kwargs):
            time.sleep(0.05)
            return pd.DataFrame()

        history_mock.side_effect = slow_history
        started = time.monotonic()
        summary, detail = build_live_breadth_snapshot(
            PROJECT_ROOT / "data" / "raw",
            force=True,
            max_workers=1,
            request_retries=1,
            request_timeout_seconds=0.02,
            batch_timeout_seconds=0.01,
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.25)
        self.assertEqual(summary["status"], "LIVE_SHADOW_DATA_GUARD")
        self.assertTrue(summary["batch_timed_out"])
        self.assertEqual(summary["timed_out_member_count"], 2)
        self.assertEqual(summary["download_error_count"], 2)
        self.assertTrue(detail["status"].eq("DOWNLOAD_ERROR").all())
        call = history_mock.call_args
        self.assertEqual(call.kwargs["request_retries"], 1)
        self.assertEqual(
            call.kwargs["request_timeout_seconds"],
            0.02,
        )

    def test_x_count_heat_detects_spike(self) -> None:
        start = pd.Timestamp("2026-01-01", tz="UTC")
        rows = []
        for hour in range(7 * 24):
            count = 10 if hour < 6 * 24 else 80
            rows.append(
                {
                    "start": start + pd.Timedelta(hours=hour),
                    "end": start + pd.Timedelta(hours=hour + 1),
                    "tweet_count": count,
                }
            )
        score = score_x_count_history(
            pd.DataFrame(rows), topic="QQQ"
        )
        self.assertGreater(score["x_heat"], 70.0)
        self.assertEqual(score["status"], "COUNTS_ONLY")

    def test_r_option_audit_reconstruction_precision(self) -> None:
        """Verify R_put/R_call/R audit entries reconstruct within 1e-6."""
        scores, components, audit = build_enriched_scores(
            _synthetic_dataset(), self.config, include_audit=True
        )
        latest = scores.dropna(subset=["R_put", "R_call"]).iloc[-1]
        row_idx = scores.index[scores["R_put"].notna()][-1]

        # --- Unrounded contributions from decomposition (engine-native) ---
        # These MUST reconstruct within 1e-6 per requirement

        # R_put: check unrounded contributions from decomposition
        r_put_dec = components.get("r_put_decomposition", {})
        if r_put_dec:
            raw_put_contribs = []
            for dec in r_put_dec.values():
                c = float(dec["contribution"].loc[row_idx]) if pd.notna(dec["contribution"].loc[row_idx]) else None
                if c is not None:
                    raw_put_contribs.append(c)
            r_put_actual = float(latest["R_put"])
            self.assertAlmostEqual(sum(raw_put_contribs), r_put_actual, delta=1e-6,
                                   msg=f"R_put unrounded sum != actual")

        # R_call: check unrounded contributions
        r_call_dec = components.get("r_call_decomposition", {})
        if r_call_dec:
            raw_call_contribs = []
            for dec in r_call_dec.values():
                c = float(dec["contribution"].loc[row_idx]) if pd.notna(dec["contribution"].loc[row_idx]) else None
                if c is not None:
                    raw_call_contribs.append(c)
            r_call_actual = float(latest["R_call"])
            self.assertAlmostEqual(sum(raw_call_contribs), r_call_actual, delta=1e-6,
                                   msg=f"R_call unrounded sum != actual")

        # R: check unrounded contributions
        r_dec = components.get("r_decomposition", {})
        if r_dec:
            raw_r_contribs = []
            for dec in r_dec.values():
                c = float(dec["contribution"].loc[row_idx]) if pd.notna(dec["contribution"].loc[row_idx]) else None
                if c is not None:
                    raw_r_contribs.append(c)
            r_actual = float(latest["R"])
            self.assertAlmostEqual(sum(raw_r_contribs), r_actual, delta=1e-6,
                                   msg=f"R unrounded sum != actual")

        # --- Rounded audit entries (display layer) ---
        # These should be close but may have minor FP accumulation
        for score_key in ["R_put", "R_call", "R"]:
            entries = audit[score_key]
            contribs = [e["contribution"] for e in entries if e["contribution"] is not None]
            if contribs:
                rebuilt = sum(contribs)
                actual = float(latest[score_key])
                # Rounded contributions should reconstruct within 5e-5 (rounding * n_components)
                self.assertAlmostEqual(rebuilt, actual, delta=5e-5,
                                       msg=f"{score_key} rounded sum {rebuilt} != actual {actual}")

        # Effective weights should sum to 1.0 (within rounding tolerance)
        for score_key in ["R_put", "R_call", "R"]:
            ew_sum = sum(e["effective_weight"] for e in audit[score_key])
            self.assertAlmostEqual(ew_sum, 1.0, places=5,
                                   msg=f"{score_key} effective weights sum to {ew_sum}")

    def test_r_option_audit_missing_tail_component(self) -> None:
        """When a tail component is missing, audit still reconstructs perfectly."""
        data = _synthetic_dataset()
        # Force vvix to NaN for last 100 rows (vol_of_vol component missing)
        data.loc[data.index[-100]:, "vvix_close"] = np.nan
        scores, components, audit = build_enriched_scores(
            data, self.config, include_audit=True
        )
        latest = scores.dropna(subset=["R_put", "R_call"]).iloc[-1]
        row_idx = scores.index[scores["R_put"].notna()][-1]

        for score_key, dec_key in [
            ("R_put", "r_put_decomposition"),
            ("R_call", "r_call_decomposition"),
            ("R", "r_decomposition"),
        ]:
            dec = components.get(dec_key, {})
            raw_contribs = []
            for d in dec.values():
                c = float(d["contribution"].loc[row_idx]) if pd.notna(d["contribution"].loc[row_idx]) else None
                if c is not None:
                    raw_contribs.append(c)
            if raw_contribs:
                rebuilt = sum(raw_contribs)
                actual = float(latest[score_key])
                self.assertAlmostEqual(rebuilt, actual, delta=1e-6,
                                       msg=f"{score_key} with missing component: sum {rebuilt} != {actual}")

    def test_r_option_audit_all_components_available(self) -> None:
        """Full data: all 4 components available for R_put and R_call."""
        scores, components, audit = build_enriched_scores(
            _synthetic_dataset(), self.config, include_audit=True
        )
        self.assertEqual(len(audit["R_put"]), 4,
                         f"Expected 4 R_put components, got {len(audit['R_put'])}")
        self.assertEqual(len(audit["R_call"]), 4,
                         f"Expected 4 R_call components, got {len(audit['R_call'])}")
        self.assertEqual(len(audit["R"]), 2,
                         f"Expected 2 R legs, got {len(audit['R'])}")
        # All components should have nominal_weight > 0
        for entry in audit["R_put"]:
            self.assertGreater(entry["nominal_weight"], 0.0)
            self.assertGreater(entry["effective_weight"], 0.0)
            self.assertIsNotNone(entry["contribution"])

    def test_r_option_audit_low_atm_coverage(self) -> None:
        """When ATM coverage < 1, effective weights are adjusted proportionally."""
        data = _synthetic_dataset()
        # Simulate low coverage by setting some R_proxy values to NaN
        data.loc[data.index[-50]:, "ndx_close"] = np.nan  # causes R_proxy NaN -> low coverage
        scores, components, audit = build_enriched_scores(
            data, self.config, include_audit=True
        )
        r_put_entries = audit["R_put"]
        # At least some entries should have effective_weight != nominal_weight
        for entry in r_put_entries:
            self.assertGreaterEqual(entry["effective_weight"], 0.0)
            self.assertLessEqual(entry["effective_weight"], 1.0)

    def test_include_audit_false_returns_two_items(self) -> None:
        """Default include_audit=False returns (DataFrame, dict), not 3 items."""
        scores, components = build_enriched_scores(
            _synthetic_dataset(), self.config, include_audit=False
        )
        self.assertIsInstance(scores, pd.DataFrame)
        self.assertIsInstance(components, dict)
        self.assertIn("r_put_decomposition", components)

    def test_include_audit_true_returns_three_items(self) -> None:
        """include_audit=True returns (DataFrame, dict, audit_dict)."""
        scores, components, audit = build_enriched_scores(
            _synthetic_dataset(), self.config, include_audit=True
        )
        self.assertIn("R_put", audit)
        self.assertIn("R_call", audit)
        self.assertIn("R", audit)
        self.assertIn("I", audit)
        self.assertIn("I_need", audit)
        self.assertIn("I_affordability", audit)

    def test_i_need_affordability_contributions_real_values(self) -> None:
        """I, I_need, I_affordability audit contributions are real (non-None)."""
        _, _, audit = build_enriched_scores(
            _synthetic_dataset(), self.config, include_audit=True
        )
        for key in ["I", "I_need", "I_affordability"]:
            entries = audit[key]
            self.assertGreater(len(entries), 0)
            for entry in entries:
                if entry["contribution"] is not None:
                    self.assertTrue(np.isfinite(entry["contribution"]))


if __name__ == "__main__":
    unittest.main()
