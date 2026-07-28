from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_INTEGRATION = PROJECT_ROOT / "integrations" / "hermes"
if str(HERMES_INTEGRATION) not in sys.path:
    sys.path.insert(0, str(HERMES_INTEGRATION))

from haven_market_model import register  # noqa: E402
from haven_market_model.tools import (  # noqa: E402
    close_update_once,
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
                    "R_put": 71.0,
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
                first = close_update_once(force_window=True)
                second = close_update_once(force_window=True)
        self.assertIn("避风港 v0.4 收盘更新", first)
        self.assertEqual(second, "")


if __name__ == "__main__":
    unittest.main()
