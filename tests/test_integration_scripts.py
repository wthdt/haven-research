"""Integration tests that run the actual production scripts.

These verify backward-compatible return interface for all entry points.
"""
import subprocess
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
_HERMES_PYTHON = os.environ.get(
    "HAVEN_TEST_PYTHON",
    "/Users/wth/.hermes/hermes-agent/venv/bin/python3",
)


def _run_script(name: str) -> subprocess.CompletedProcess:
    path = SCRIPTS_DIR / name
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    result = subprocess.run(
        [_HERMES_PYTHON, str(path)],
        capture_output=True,
        text=True,
        timeout=360,
        env=env,
        cwd=PROJECT_ROOT,
    )
    return result


class TestScriptInterface:
    def test_insurance_recommended_v0_3(self):
        result = _run_script("run_insurance_recommended_v0_3.py")
        assert result.returncode == 0, (
            f"Exit {result.returncode}: {result.stderr[-500:] or result.stdout[-500:]}"
        )

    def test_enriched_indicators_v0_4(self):
        result = _run_script("run_enriched_indicators_v0_4.py")
        assert result.returncode == 0, (
            f"Exit {result.returncode}: {result.stderr[-500:] or result.stdout[-500:]}"
        )
        assert "breadth_stress" in result.stdout

    def test_insurance_model_v0_3(self):
        result = _run_script("run_insurance_model_v0_3.py")
        assert result.returncode == 0, (
            f"Exit {result.returncode}: {result.stderr[-500:] or result.stdout[-500:]}"
        )

    def test_options_proxy_v0_2(self):
        result = _run_script("run_options_proxy_v0_2.py")
        assert result.returncode == 0, (
            f"Exit {result.returncode}: {result.stderr[-500:] or result.stdout[-500:]}"
        )

    def test_ten_year(self):
        result = _run_script("run_ten_year.py")
        assert result.returncode == 0, (
            f"Exit {result.returncode}: {result.stderr[-500:] or result.stdout[-500:]}"
        )

    def test_ten_year_has_metrics(self):
        result = _run_script("run_ten_year.py")
        # Column headers are lowercase in the output table
        assert "cagr" in result.stdout
        assert "max_drawdown" in result.stdout
        assert "sharpe" in result.stdout
