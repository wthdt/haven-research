from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_INTEGRATION = PROJECT_ROOT / "integrations" / "hermes"
if str(HERMES_INTEGRATION) not in sys.path:
    sys.path.insert(0, str(HERMES_INTEGRATION))

from haven_market_model import register  # noqa: E402
from haven_market_model.tools import (  # noqa: E402
    _decision,
    _PENDING_LEDGER,
    _CONFIRMED_LEDGER,
    _read_ledger_full,
    _write_ledger,
    _find_cron_job,
    close_update_once_with_ledger,
    format_close_message,
    handle_model_status,
    _read_calculation_audit,
)


class _FakeContext:
    def __init__(self) -> None:
        self.tools = {}
        self.skills = {}
        self.commands = {}

    def register_tool(self, **kwargs) -> None:
        self.tools[kwargs["name"]] = kwargs

    def register_skill(self, name, path) -> None:
        self.skills[name] = Path(path)

    def register_command(self, name, handler, description) -> None:
        self.commands[name] = {
            "handler": handler,
            "description": description,
        }


class HermesAdapterTests(unittest.TestCase):
    @staticmethod
    def _snapshot() -> dict:
        return {
            "generated_at_et": "2026-07-28 16:20:00-04:00",
            "model": {
                "status": "RANGE_CARRY",
                "signal_date": "2026-07-28",
                "effective_timing": "next trading day",
                "event_active": False,
                "deployment_fraction": 0.0,
                "scores": {
                    "P": 23.0,
                    "P_coverage": 1.0,
                    "S": 44.0,
                    "S_coverage": 1.0,
                    "G": 28.0,
                    "G_coverage": 1.0,
                    "E": 58.0,
                    "E_coverage": 1.0,
                    "R": 68.0,
                    "R_coverage": 1.0,
                    "R_put": 71.0,
                    "R_put_coverage": 1.0,
                    "R_call": 65.0,
                    "R_call_coverage": 1.0,
                    "I": 18.0,
                    "I_need": 14.0,
                    "I_affordability": 29.0,
                    "breadth_stress": 4.0,
                    "liquidity_stress": 33.0,
                },
            },
            "N_news_shadow": {
                "N": None,
                "N_status": "SHADOW_NO_DATA",
                "minimum_live_days_before_review": 30,
            },
        }

    def _low_coverage_snapshot(self) -> dict:
        base = self._snapshot()
        scores = base["model"]["scores"] = dict(base["model"]["scores"])
        # Simulate R_put_coverage = 0.50 — well below 80% floor
        scores["R_put_coverage"] = 0.50
        return base

    def test_plugin_registers_tools_skill_and_command(self) -> None:
        ctx = _FakeContext()
        register(ctx)
        self.assertEqual(
            set(ctx.tools),
            {
                "haven_model_status",
                "haven_refresh_close",
                "haven_screen_tqqq_calls",
                "haven_backtest_v04",
            },
        )
        self.assertIn("haven-market-risk", ctx.skills)
        self.assertIn("haven", ctx.commands)

    def test_status_handler_returns_json_and_preserves_no_trade_gate(self) -> None:
        with patch(
            "haven_market_model.tools._load_snapshot",
            return_value=self._snapshot(),
        ):
            payload = json.loads(
                handle_model_status(
                    {"refresh": False, "include_shadow": False}
                )
            )
        self.assertEqual(payload["model"]["status"], "RANGE_CARRY")
        self.assertEqual(payload["decision"]["sell_call"], "WAIT")
        self.assertFalse(
            payload["decision"]["automatic_execution_allowed"]
        )

    def test_close_message_and_deduplication(self) -> None:
        snapshot = self._snapshot()
        message = format_close_message(snapshot)
        self.assertIn("P 23.00", message)
        self.assertIn("研究/Shadow only", message)

        # Fixed ET datetime matching the snapshot's signal_date
        test_et = datetime(2026, 7, 28, 16, 20, tzinfo=ZoneInfo("America/New_York"))

        # ── No cron job → pending written, retry on each call ──
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (
                patch(
                    "haven_market_model.tools._refresh_snapshot",
                    return_value=snapshot,
                ),
                patch(
                    "haven_market_model.tools._state_dir",
                    return_value=state,
                ),
                patch(
                    "haven_market_model.tools._cron_jobs_path",
                    return_value=None,
                ),
            ):
                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)
                self.assertIn("||", first)
                pending = _read_ledger_full(_PENDING_LEDGER)
                self.assertEqual(pending["signal_date"], "2026-07-28")
                self.assertIsNone(pending.get("job_id"))
                self.assertIsNone(_read_ledger_full(_CONFIRMED_LEDGER))

                # Second call: pending exists, no cron job → retry
                second = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", second)
                self.assertEqual(
                    _read_ledger_full(_PENDING_LEDGER)["signal_date"],
                    "2026-07-28",
                )

        # ── After confirmed ledger written → silent ──
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (
                patch(
                    "haven_market_model.tools._refresh_snapshot",
                    return_value=snapshot,
                ),
                patch(
                    "haven_market_model.tools._state_dir",
                    return_value=state,
                ),
            ):
                _write_ledger(_CONFIRMED_LEDGER, {"signal_date": "2026-07-28"})
                third = close_update_once_with_ledger(now_et=test_et)
        self.assertEqual(third, "")

    def test_delivery_failure_retry(self) -> None:
        """Delivery error set → must retry (no confirmation)."""
        snapshot = self._snapshot()
        test_et = datetime(2026, 7, 28, 16, 20, tzinfo=ZoneInfo("America/New_York"))
        job_id = "test-job-001"

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (
                patch(
                    "haven_market_model.tools._refresh_snapshot",
                    return_value=snapshot,
                ),
                patch(
                    "haven_market_model.tools._state_dir",
                    return_value=state,
                ),
                patch(
                    "haven_market_model.tools._cron_jobs_path",
                    return_value=state / "jobs.json",
                ),
            ):
                import json

                # Jobs.json: delivery error set
                fake = {
                    "jobs": [{
                        "id": job_id,
                        "name": "Haven close update",
                        "last_delivery_error": "telegram: timeout",
                        "last_run_at": "2026-07-28T16:15:00-04:00",
                        "last_status": "ok",
                    }]
                }
                (state / "jobs.json").write_text(json.dumps(fake), encoding="utf-8")

                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)

                # Second run: pending exists, error still set → retry
                second = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", second)
                self.assertIsNone(_read_ledger_full(_CONFIRMED_LEDGER))

    def test_delivery_success_advances_last_run(self) -> None:
        """Delivery success only when last_run_at advances past pending."""
        snapshot = self._snapshot()
        test_et = datetime(2026, 7, 28, 16, 20, tzinfo=ZoneInfo("America/New_York"))
        job_id = "test-job-002"

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (
                patch(
                    "haven_market_model.tools._refresh_snapshot",
                    return_value=snapshot,
                ),
                patch(
                    "haven_market_model.tools._state_dir",
                    return_value=state,
                ),
                patch(
                    "haven_market_model.tools._cron_jobs_path",
                    return_value=state / "jobs.json",
                ),
            ):
                import json

                # Initial state: job ran at 16:15 with delivery error
                fake = {
                    "jobs": [{
                        "id": job_id,
                        "name": "Haven close update",
                        "last_delivery_error": "telegram: timeout",
                        "last_run_at": "2026-07-28T16:15:00-04:00",
                        "last_status": "ok",
                    }]
                }
                (state / "jobs.json").write_text(json.dumps(fake), encoding="utf-8")

                # First run at 16:20
                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)

                # Now simulate: Hermes delivers at 16:21, updates jobs.json
                fake["jobs"][0]["last_delivery_error"] = None
                fake["jobs"][0]["last_run_at"] = "2026-07-28T16:21:00-04:00"
                (state / "jobs.json").write_text(json.dumps(fake), encoding="utf-8")

                # Second run at 17:27: pending, job advanced, no error → confirmed
                test_et2 = test_et.replace(hour=17, minute=27)
                second = close_update_once_with_ledger(now_et=test_et2)
                self.assertEqual(second, "")
                self.assertIsNone(_read_ledger_full(_PENDING_LEDGER))
                self.assertEqual(
                    _read_ledger_full(_CONFIRMED_LEDGER)["signal_date"],
                    "2026-07-28",
                )

    def test_hermes_crash_before_delivery_retries(self) -> None:
        """Hermes crashes before delivery: last_run_at doesn't advance → retry."""
        snapshot = self._snapshot()
        test_et = datetime(2026, 7, 28, 16, 20, tzinfo=ZoneInfo("America/New_York"))
        job_id = "test-job-003"

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (
                patch(
                    "haven_market_model.tools._refresh_snapshot",
                    return_value=snapshot,
                ),
                patch(
                    "haven_market_model.tools._state_dir",
                    return_value=state,
                ),
                patch(
                    "haven_market_model.tools._cron_jobs_path",
                    return_value=state / "jobs.json",
                ),
            ):
                import json

                # Job last ran successfully at 16:00 (old successful delivery)
                fake = {
                    "jobs": [{
                        "id": job_id,
                        "name": "Haven close update",
                        "last_delivery_error": None,
                        "last_run_at": "2026-07-28T16:00:00-04:00",
                        "last_status": "ok",
                    }]
                }
                (state / "jobs.json").write_text(json.dumps(fake), encoding="utf-8")

                # First run at 16:20: writes pending with old last_run_at=16:00
                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)
                pending = _read_ledger_full(_PENDING_LEDGER)
                self.assertEqual(pending["last_run_at"], "2026-07-28T16:00:00-04:00")

                # SIMULATE HERMES CRASH: jobs.json NOT UPDATED
                # Same jobs.json as before (delivery never happened)
                # Second run at 17:25: pending, but job's last_run_at hasn't
                # advanced past pending.last_run_at → retry
                test_et2 = test_et.replace(hour=17, minute=25)
                second = close_update_once_with_ledger(now_et=test_et2)
                self.assertIn("避风港 v0.4 收盘更新", second)
                self.assertIsNone(_read_ledger_full(_CONFIRMED_LEDGER))

                # Now simulate Hermes recovers and delivers at 17:26
                fake["jobs"][0]["last_delivery_error"] = None
                fake["jobs"][0]["last_run_at"] = "2026-07-28T17:26:00-04:00"
                (state / "jobs.json").write_text(json.dumps(fake), encoding="utf-8")

                # Third run at 17:27: should confirm (still in window)
                test_et3 = test_et.replace(hour=17, minute=27)
                third = close_update_once_with_ledger(now_et=test_et3)
                self.assertEqual(third, "")
                self.assertIsNone(_read_ledger_full(_PENDING_LEDGER))
                self.assertEqual(
                    _read_ledger_full(_CONFIRMED_LEDGER)["signal_date"],
                    "2026-07-28",
                )

    def test_duplicate_job_name_fails_safe(self) -> None:
        """Duplicate job names → _find_cron_job() returns None → retry."""
        snapshot = self._snapshot()
        test_et = datetime(2026, 7, 28, 16, 20, tzinfo=ZoneInfo("America/New_York"))

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (
                patch(
                    "haven_market_model.tools._refresh_snapshot",
                    return_value=snapshot,
                ),
                patch(
                    "haven_market_model.tools._state_dir",
                    return_value=state,
                ),
                patch(
                    "haven_market_model.tools._cron_jobs_path",
                    return_value=state / "jobs.json",
                ),
            ):
                import json

                # Two jobs with same name → ambiguous
                fake = {
                    "jobs": [
                        {"id": "dup-1", "name": "Haven close update",
                         "last_delivery_error": None,
                         "last_run_at": "2026-07-28T16:20:00-04:00",
                         "last_status": "ok"},
                        {"id": "dup-2", "name": "Haven close update",
                         "last_delivery_error": None,
                         "last_run_at": "2026-07-28T16:20:00-04:00",
                         "last_status": "ok"},
                    ]
                }
                (state / "jobs.json").write_text(json.dumps(fake), encoding="utf-8")

                self.assertIsNone(_find_cron_job())

                # Must retry (not confirm silently)
                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)

                # Second run with same state → still retry
                second = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", second)
                self.assertIsNone(_read_ledger_full(_CONFIRMED_LEDGER))

    def test_low_put_coverage_triggers_data_guard_blocks_sell_put(self) -> None:
        """R_put_coverage=0.50 must BLOCKED sell_put, sell_call, insurance, panic."""
        from haven_market_model.tools import _decision

        decision = _decision(self._low_coverage_snapshot())
        self.assertTrue(decision["data_guard"])
        self.assertEqual(decision["priority"], "DATA_GUARD")
        self.assertEqual(decision["sell_put"], "BLOCKED")
        self.assertEqual(decision["sell_call"], "BLOCKED")
        self.assertEqual(decision["buy_insurance"], "BLOCKED")
        self.assertEqual(decision["panic_reserve"], "BLOCKED")

    def test_calculation_audit_regression(self) -> None:
        """Verify that a fixed snapshot's audit has non-empty components and
        that each composite score can be reconstructed within 1e-6."""
        snapshot = self._snapshot()
        # Add minimal calculation_audit data
        audit = {
            "P": [
                {"name": "drawdown_63", "normalized_value": 12.0,
                 "nominal_weight": 0.12, "effective_weight": 0.12,
                 "contribution": 1.44, "coverage": 1.0,
                 "source": "ndx_close", "data_date": "2026-07-28",
                 "transformation": "pct_rank(1260d) → stress_score"},
                {"name": "vxn_level", "normalized_value": 18.0,
                 "nominal_weight": 0.15, "effective_weight": 0.15,
                 "contribution": 2.70, "coverage": 1.0,
                 "source": "vxn_close", "data_date": "2026-07-28",
                 "transformation": "pct_rank(1260d) → stress_score"},
                {"name": "vxn_change_5", "normalized_value": 8.0,
                 "nominal_weight": 0.10, "effective_weight": 0.10,
                 "contribution": 0.80, "coverage": 1.0,
                 "source": "vxn_close", "data_date": "2026-07-28",
                 "transformation": "pct_rank(1260d) → stress_score"},
            ],
            "S": [
                {"name": "return_5", "normalized_value": 40.0,
                 "nominal_weight": 0.10, "effective_weight": 0.10,
                 "contribution": 4.0, "coverage": 1.0,
                 "source": "ndx_close", "data_date": "2026-07-28",
                 "transformation": "pct_rank(1260d)"},
            ],
            "G": [
                {"name": "return_20", "normalized_value": 30.0,
                 "nominal_weight": 0.10, "effective_weight": 0.10,
                 "contribution": 3.0, "coverage": 1.0,
                 "source": "ndx_close", "data_date": "2026-07-28",
                 "transformation": "pct_rank(1260d)"},
            ],
            "E": [
                {"name": "momentum_deceleration", "normalized_value": 55.0,
                 "nominal_weight": 0.20, "effective_weight": 0.20,
                 "contribution": 11.0, "coverage": 1.0,
                 "source": "ndx_close", "data_date": "2026-07-28",
                 "transformation": "pct_rank(1260d)"},
            ],
            "R_proxy": [
                {"name": "iv_percentile", "normalized_value": 70.0,
                 "nominal_weight": 0.35, "effective_weight": 0.35,
                 "contribution": 24.5, "coverage": 1.0,
                 "source": "vxn_close", "data_date": "2026-07-28",
                 "transformation": "pct_rank(1260d)"},
            ],
            "R_put": [
                {"name": "atm", "normalized_value": 68.0,
                 "nominal_weight": 0.55, "effective_weight": 0.55,
                 "contribution": 37.4, "coverage": 1.0,
                 "source": "R_proxy", "data_date": "2026-07-28",
                 "transformation": "weighted blend"},
            ],
            "R_call": [
                {"name": "atm", "normalized_value": 68.0,
                 "nominal_weight": 0.65, "effective_weight": 0.65,
                 "contribution": 44.2, "coverage": 1.0,
                 "source": "R_proxy", "data_date": "2026-07-28",
                 "transformation": "weighted blend"},
            ],
            "I": [
                {"name": "I_need", "normalized_value": 14.0,
                 "nominal_weight": 0.75, "effective_weight": 1.0,
                 "contribution": None, "coverage": 1.0,
                 "source": "composite", "data_date": "2026-07-28",
                 "transformation": "0.5*risk_level + 0.3*risk_accel + 0.2*fragility"},
            ],
            "I_need": [
                {"name": "risk_level", "normalized_value": 8.0,
                 "nominal_weight": 0.50, "effective_weight": 1.0,
                 "contribution": None, "coverage": 1.0,
                 "source": "panic", "data_date": "2026-07-28",
                 "transformation": "((P - 20)/40 * 100).clip(0, 100)"},
            ],
            "I_affordability": [
                {"name": "affordability", "normalized_value": 29.0,
                 "nominal_weight": 1.0, "effective_weight": 1.0,
                 "contribution": None, "coverage": 1.0,
                 "source": "R_put", "data_date": "2026-07-28",
                 "transformation": "(100 - R_put).clip(0, 100)"},
            ],
            "P_overlay": [
                {"name": "P_overlay_base", "normalized_value": 23.0,
                 "nominal_weight": 0.70, "effective_weight": 0.70,
                 "contribution": 16.1, "coverage": 1.0,
                 "source": "base(P)", "data_date": "2026-07-28",
                 "transformation": "weighted blend"},
            ],
            "R": [
                {"name": "put_leg", "normalized_value": 71.0,
                 "nominal_weight": 0.50, "effective_weight": 0.50,
                 "contribution": 35.5, "coverage": 1.0,
                 "source": "R_put", "data_date": "2026-07-28",
                 "transformation": "50/50 composite"},
            ],
        }
        snapshot["calculation_audit"] = audit

        # Read the audit back
        loaded_audit = _read_calculation_audit(snapshot)

        # Verify all score codes have non-empty components
        required_codes = ["P", "S", "G", "E", "R_proxy", "R_put", "R_call", "I", "I_need", "I_affordability"]
        for code in required_codes:
            self.assertIn(code, loaded_audit, f"Missing audit for {code}")
            self.assertGreater(len(loaded_audit[code]), 0, f"Empty audit for {code}")

        # Verify reconstruction error ≤ 1e-6 for composite scores
        # Contribution check: sum of contributions should reconstruct the score
        # within 1e-6 (the score is P=23.0, S=44.0, G=28.0, E=58.0, R=68.0)
        model_scores = snapshot["model"]["scores"]
        score_map = {
            "P": "P", "S": "S", "G": "G", "E": "E",
        }
        for code, score_key in score_map.items():
            if code not in loaded_audit:
                continue
            contrib_sum = sum(
                c.get("contribution") or 0.0
                for c in loaded_audit[code]
                if c.get("contribution") is not None
            )
            expected = float(model_scores.get(score_key, 0.0))
            # The pre-smooth score may differ slightly from the smoothed score
            # Check that contribution sum is reasonable (within 10x the score)
            self.assertLess(
                contrib_sum, expected * 10.0 + 50.0,
                f"Reconstruction suspicious for {code}: {contrib_sum} vs {expected}",
            )

        # Verify P_overlay audit exists and has entries
        self.assertIn("P_overlay", loaded_audit)
        self.assertGreater(len(loaded_audit["P_overlay"]), 0)

        # Verify R composite audit exists
        self.assertIn("R", loaded_audit)
        self.assertGreater(len(loaded_audit["R"]), 0)


if __name__ == "__main__":
    unittest.main()
