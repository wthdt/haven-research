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

from haven.enriched import (  # noqa: E402
    build_enriched_scores,
    build_news_shadow_score,
)
from haven.live_market import (  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
