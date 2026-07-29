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
    _read_ledger,
    _write_ledger,
    close_update_once_with_ledger,
    format_close_message,
    handle_model_status,
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

        # Use a fixed ET datetime matching the snapshot's signal_date
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
                # No cron job exists → _read_job_delivery_status() returns None
                patch(
                    "haven_market_model.tools._cron_jobs_path",
                    return_value=None,
                ),
            ):
                # ── First call: writes pending ledger, outputs message ──
                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)
                self.assertIn("||", first)
                self.assertEqual(
                    _read_ledger(_PENDING_LEDGER), "2026-07-28"
                )
                self.assertIsNone(_read_ledger(_CONFIRMED_LEDGER))

                # ── Second call: pending exists, no cron job → retry ──
                second = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", second)
                # Still pending (not promoted since no cron job to check)
                self.assertEqual(
                    _read_ledger(_PENDING_LEDGER), "2026-07-28"
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
                _write_ledger(_CONFIRMED_LEDGER, "2026-07-28")
                third = close_update_once_with_ledger(now_et=test_et)
        self.assertEqual(third, "")

    def test_delivery_failure_retry(self) -> None:
        """Simulate: first run (pending), delivery fails, second run retries."""
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
                # Simulate: cron job exists with delivery error
                patch(
                    "haven_market_model.tools._cron_jobs_path",
                    return_value=state / "jobs.json",
                ),
            ):
                # Create a fake jobs.json with delivery error
                import json
                fake_jobs = {
                    "jobs": [{
                        "name": "Haven close update",
                        "last_delivery_error": "telegram: timeout",
                        "last_run_at": "2026-07-28T16:20:00-04:00",
                        "last_status": "ok",
                    }]
                }
                (state / "jobs.json").write_text(
                    json.dumps(fake_jobs), encoding="utf-8"
                )

                # First run: writes pending ledger
                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)

                # Second run: pending exists, delivery_error set → retry
                second = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", second)
                # Still pending (not promoted because error not cleared)
                self.assertEqual(
                    _read_ledger(_PENDING_LEDGER), "2026-07-28"
                )
                self.assertIsNone(_read_ledger(_CONFIRMED_LEDGER))

    def test_delivery_success_promotes_to_confirmed(self) -> None:
        """Simulate: first run (pending), next run finds delivery ok → silent."""
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

                # First run: writes pending ledger
                first = close_update_once_with_ledger(now_et=test_et)
                self.assertIn("避风港 v0.4 收盘更新", first)
                self.assertEqual(
                    _read_ledger(_PENDING_LEDGER), "2026-07-28"
                )

                # Simulate Hermes cron delivery success (last_delivery_error = null)
                fake_jobs = {
                    "jobs": [{
                        "name": "Haven close update",
                        "last_delivery_error": None,
                        "last_run_at": "2026-07-28T16:20:00-04:00",
                        "last_status": "ok",
                    }]
                }
                (state / "jobs.json").write_text(
                    json.dumps(fake_jobs), encoding="utf-8"
                )

                # Second run: pending exists, delivery OK → promote to confirmed
                second = close_update_once_with_ledger(now_et=test_et)
                self.assertEqual(second, "")
                self.assertIsNone(_read_ledger(_PENDING_LEDGER))
                self.assertEqual(
                    _read_ledger(_CONFIRMED_LEDGER), "2026-07-28"
                )

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


if __name__ == "__main__":
    unittest.main()
