from __future__ import annotations

import copy
import csv
import json
import math
import os
import subprocess
import sys
import threading
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PLUGIN_DIR = Path(__file__).resolve().parent
_RUN_LOCK = threading.Lock()
_REQUIRED_SCORE_CODES = ("P", "S", "G", "E", "R", "R_put", "R_call")
# I-related scores: no _coverage field in model output, check presence only
_I_MISSING_CODES = ("I", "I_need", "I_affordability")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        import numpy as np

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            number = float(value)
            return number if math.isfinite(number) else None
        if isinstance(value, np.bool_):
            return bool(value)
    except ImportError:
        pass
    return value


def _dumps(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


def _hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".hermes").resolve()
    )


def _engine_root() -> Path:
    configured = os.environ.get("HAVEN_RESEARCH_ROOT", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())

    path_file = PLUGIN_DIR / "engine_path.json"
    if path_file.exists():
        try:
            payload = json.loads(path_file.read_text(encoding="utf-8"))
            candidates.append(Path(str(payload["engine_root"])).expanduser())
        except (KeyError, OSError, ValueError, TypeError):
            pass

    candidates.append(PLUGIN_DIR / "engine")
    candidates.extend(PLUGIN_DIR.parents)
    for candidate in candidates:
        root = candidate.resolve()
        if (
            (root / "scripts" / "run_live_shadow_v0_4.py").is_file()
            and (root / "src" / "haven").is_dir()
            and (root / "config" / "haven_v0_4_enriched_indicators.yaml").is_file()
        ):
            return root
    raise RuntimeError(
        "Haven engine not found. Set HAVEN_RESEARCH_ROOT or run the "
        "Hermes adapter installer so engine_path.json is created."
    )


def _snapshot_path(root: Path) -> Path:
    return (
        root
        / "outputs"
        / "ten_year_v0_4_enriched"
        / "live"
        / "current_snapshot.json"
    )


def _load_snapshot(root: Path) -> dict[str, Any]:
    path = _snapshot_path(root)
    if not path.exists():
        raise FileNotFoundError(
            f"No v0.4 snapshot at {path}; refresh the close model first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("The v0.4 snapshot is not a JSON object")
    return payload


def _run_script(
    root: Path,
    relative_script: str,
    *,
    timeout_seconds: int,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(root / relative_script)],
        cwd=str(root),
        env=env,
        text=True,
        capture_output=True,
        timeout=max(int(timeout_seconds), 30),
        check=False,
    )


def _refresh_snapshot(
    *,
    force_market_data: bool = True,
    force_breadth: bool = True,
    force_option_chain: bool = True,
) -> dict[str, Any]:
    root = _engine_root()
    env = {
        "HAVEN_FORCE_MARKET_DATA": "1" if force_market_data else "0",
        "HAVEN_FORCE_BREADTH": "1" if force_breadth else "0",
        "HAVEN_FORCE_OPTION_CHAIN": "1" if force_option_chain else "0",
    }
    with _RUN_LOCK:
        completed = _run_script(
            root,
            "scripts/run_live_shadow_v0_4.py",
            timeout_seconds=int(
                os.environ.get("HAVEN_REFRESH_TIMEOUT_SECONDS", "1800")
            ),
            extra_env=env,
        )
    if completed.returncode != 0:
        error_tail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            "v0.4 close refresh failed"
            + (f": {error_tail[-2000:]}" if error_tail else "")
        )
    return _load_snapshot(root)


def _load_option_config(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is missing from the Hermes Python environment"
        ) from exc

    base = yaml.safe_load(
        (
            root / "config" / "haven_v0_2_options_proxy.yaml"
        ).read_text(encoding="utf-8")
    )
    insurance = yaml.safe_load(
        (
            root / "config" / "haven_v0_3_insurance_model.yaml"
        ).read_text(encoding="utf-8")
    )
    enriched = yaml.safe_load(
        (
            root / "config" / "haven_v0_4_enriched_indicators.yaml"
        ).read_text(encoding="utf-8")
    )
    option_config = copy.deepcopy(base["options_proxy"])

    def merge(target: dict[str, Any], overlay: dict[str, Any]) -> None:
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    merge(option_config, insurance.get("options_proxy", {}))
    merge(option_config, enriched.get("options_proxy", {}))
    return option_config, insurance["options_proxy"]["insurance"]


def _float(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _decision(snapshot: dict[str, Any]) -> dict[str, Any]:
    root = _engine_root()
    model = dict(snapshot.get("model") or {})
    scores = dict(model.get("scores") or {})
    state = str(model.get("status") or "UNKNOWN")
    coverage = {
        code: _float(scores.get(f"{code}_coverage"), 0.0)
        for code in _REQUIRED_SCORE_CODES
    }
    missing = [
        code
        for code in _REQUIRED_SCORE_CODES
        if not math.isfinite(_float(scores.get(code)))
    ]
    missing.extend(
        code
        for code in _I_MISSING_CODES
        if not math.isfinite(_float(scores.get(code)))
    )
    data_guard = (
        "DATA_GUARD" in state
        or bool(missing)
        or min(coverage.values(), default=0.0) < 0.80
    )
    base = {
        "priority": "DATA_GUARD" if data_guard else "MODEL_STATE",
        "data_guard": data_guard,
        "missing_scores": missing,
        "minimum_required_coverage": min(
            coverage.values(),
            default=0.0,
        ),
        "research_only": True,
        "automatic_execution_allowed": False,
        "paper_live_modified": False,
        "tqqq_paper_live_allowed": False,
    }
    if data_guard:
        return base | {
            "panic_reserve": "BLOCKED",
            "sell_put": "BLOCKED",
            "sell_call": "BLOCKED",
            "buy_insurance": "BLOCKED",
            "reason": "Required score data failed the 80% coverage floor",
        }

    option_config, insurance_config = _load_option_config(root)
    src_root = root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    import pandas as pd

    from haven.covered_call import covered_call_gate
    from haven.options_proxy import _csp_terms

    decision_row = pd.Series(
        {
            "state": state,
            "P": _float(scores.get("P")),
            "S": _float(scores.get("S")),
            "G": _float(scores.get("G")),
            "E": _float(scores.get("E")),
            "R_proxy": _float(scores.get("R")),
            "R_put": _float(scores.get("R_put")),
            "R_call": _float(scores.get("R_call")),
            "breadth_stress": _float(scores.get("breadth_stress")),
            "liquidity_stress": _float(scores.get("liquidity_stress")),
        }
    )
    put_terms = _csp_terms(decision_row, option_config)
    # Model status is position-agnostic. Use a large notional share count so
    # the portfolio-level gate is not confused with a user's whole-contract
    # granularity; the TQQQ screener applies the real share count separately.
    call_gate = covered_call_gate(
        model,
        option_config,
        shares=10_000,
    )

    allowed_insurance_states = set(
        insurance_config.get("allowed_entry_states", [])
    )
    insurance_open = (
        state in allowed_insurance_states
        and _float(scores.get("I_need")) >= float(
            insurance_config.get("trigger_i_need_min", 55.0)
        )
        and _float(scores.get("I")) >= float(
            insurance_config.get("trigger_i_min", 48.0)
        )
        and _float(scores.get("I_affordability")) >= float(
            insurance_config.get(
                "trigger_i_affordability_min",
                10.0,
            )
        )
        and _float(scores.get("R_put")) <= float(
            insurance_config.get("entry_r_proxy_max", 90.0)
        )
    )
    n_shadow = dict(snapshot.get("N_news_shadow") or {})
    return base | {
        "panic_reserve": (
            "EVENT_ACTIVE"
            if bool(model.get("event_active"))
            else "HOLD"
        ),
        "panic_deployment_fraction": _float(
            model.get("deployment_fraction"),
            0.0,
        ),
        "sell_put": (
            "RESEARCH_GATE_OPEN" if put_terms is not None else "WAIT"
        ),
        "sell_put_reason": (
            "v0.4 state/R_put/E safety gates passed"
            if put_terms is not None
            else "v0.4 state/R_put/E safety gates not satisfied"
        ),
        "sell_call": (
            "RESEARCH_GATE_OPEN"
            if call_gate.get("eligible")
            else "WAIT"
        ),
        "sell_call_gate": call_gate,
        "buy_insurance": (
            "RESEARCH_GATE_OPEN" if insurance_open else "WAIT"
        ),
        "news_model": {
            "status": n_shadow.get("N_status", "SHADOW_NO_DATA"),
            "score": n_shadow.get("N"),
            "affects_position": False,
            "minimum_live_days_before_review": n_shadow.get(
                "minimum_live_days_before_review",
                30,
            ),
        },
    }


def _status_payload(
    snapshot: dict[str, Any],
    *,
    include_shadow: bool,
) -> dict[str, Any]:
    payload = {
        "generated_at_et": snapshot.get("generated_at_et"),
        "model": snapshot.get("model"),
        "decision": _decision(snapshot),
        "calculation_audit": _build_calculation_audit(snapshot),
        "guardrails": {
            "research_only": True,
            "signal_effective_timing": "next trading day",
            "covered_call_automatic_execution": False,
            "tqqq_paper_live": False,
            "news_affects_position": False,
        },
    }
    if include_shadow:
        payload.update(
            {
                "real_breadth_shadow": snapshot.get(
                    "real_breadth_shadow"
                ),
                "real_qqq_chain_shadow": snapshot.get(
                    "real_qqq_chain_shadow"
                ),
                "N_news_shadow": snapshot.get("N_news_shadow"),
            }
        )
    return payload


def handle_model_status(args: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    try:
        if bool(args.get("refresh", False)):
            snapshot = _refresh_snapshot()
        else:
            try:
                snapshot = _load_snapshot(_engine_root())
            except FileNotFoundError:
                snapshot = _refresh_snapshot()
        return _dumps(
            _status_payload(
                snapshot,
                include_shadow=bool(args.get("include_shadow", True)),
            )
        )
    except Exception as exc:
        return _dumps(
            {
                "status": "DATA_GUARD",
                "error": f"{type(exc).__name__}: {exc}",
                "automatic_execution_allowed": False,
            }
        )


def handle_refresh_close(args: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    try:
        snapshot = _refresh_snapshot(
            force_market_data=bool(
                args.get("force_market_data", True)
            ),
            force_breadth=bool(args.get("force_breadth", True)),
            force_option_chain=bool(
                args.get("force_option_chain", True)
            ),
        )
        return _dumps(_status_payload(snapshot, include_shadow=True))
    except Exception as exc:
        return _dumps(
            {
                "status": "DATA_GUARD",
                "error": f"{type(exc).__name__}: {exc}",
                "automatic_execution_allowed": False,
            }
        )


def handle_screen_tqqq_calls(
    args: dict[str, Any],
    **kwargs: Any,
) -> str:
    del kwargs
    try:
        root = _engine_root()
        snapshot = (
            _refresh_snapshot()
            if bool(args.get("refresh_model", False))
            else _load_snapshot(root)
        )
        src_root = root / "src"
        if str(src_root) not in sys.path:
            sys.path.insert(0, str(src_root))
        from haven.covered_call import screen_covered_calls
        from haven.live_market import fetch_nasdaq_option_chain

        option_config, _ = _load_option_config(root)
        metadata, chain = fetch_nasdaq_option_chain(
            "TQQQ",
            root / "data" / "raw",
            force=bool(args.get("force_option_chain", True)),
        )
        qqq_shadow = dict(
            snapshot.get("real_qqq_chain_shadow") or {}
        )
        rate = _float(qqq_shadow.get("rate"), 0.04)
        result = screen_covered_calls(
            chain,
            model=dict(snapshot.get("model") or {}),
            option_config=option_config,
            shares=int(args["shares"]),
            rate=rate,
            dividend_yield=0.0,
            cost_basis=(
                float(args["cost_basis"])
                if args.get("cost_basis") is not None
                else None
            ),
            historical_premium=float(
                args.get("historical_premium", 0.0)
            ),
            maximum_candidates=int(
                args.get("maximum_candidates", 5)
            ),
        )
        result["chain_metadata"] = metadata
        return _dumps(result)
    except Exception as exc:
        return _dumps(
            {
                "status": "LIVE_CHAIN_DATA_GUARD",
                "error": f"{type(exc).__name__}: {exc}",
                "candidates": [],
                "research_only": True,
                "automation_allowed": False,
            }
        )


def _read_csv_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def handle_backtest_v04(args: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    try:
        root = _engine_root()
        if bool(args.get("rerun", False)):
            with _RUN_LOCK:
                completed = _run_script(
                    root,
                    "scripts/run_enriched_indicators_v0_4.py",
                    timeout_seconds=int(
                        os.environ.get(
                            "HAVEN_BACKTEST_TIMEOUT_SECONDS",
                            "3600",
                        )
                    ),
                )
            if completed.returncode != 0:
                tail = (
                    completed.stderr or completed.stdout or ""
                ).strip()
                raise RuntimeError(
                    "v0.4 backtest failed"
                    + (f": {tail[-2000:]}" if tail else "")
                )
        output = root / "outputs" / "ten_year_v0_4_enriched"
        manifest_path = output / "run_manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {}
        )
        return _dumps(
            {
                "status": "READY",
                "rerun": bool(args.get("rerun", False)),
                "metrics": _read_csv_records(output / "metrics.csv"),
                "headline_comparison": _read_csv_records(
                    output / "headline_comparison.csv"
                ),
                "stress_periods": _read_csv_records(
                    output / "stress_periods.csv"
                ),
                "manifest": manifest,
                "research_only": True,
                "paper_live_modified": False,
            }
        )
    except Exception as exc:
        return _dumps(
            {
                "status": "DATA_GUARD",
                "error": f"{type(exc).__name__}: {exc}",
                "research_only": True,
            }
        )


def _fmt_score(scores: dict[str, Any], code: str) -> str:
    value = _float(scores.get(code))
    return f"{value:.2f}" if math.isfinite(value) else "N/A"


def format_close_message(snapshot: dict[str, Any]) -> str:
    model = dict(snapshot.get("model") or {})
    scores = dict(model.get("scores") or {})
    decision = _decision(snapshot)
    date = model.get("signal_date", "unknown")
    state = model.get("status", "UNKNOWN")
    n_shadow = dict(snapshot.get("N_news_shadow") or {})
    n_value = n_shadow.get("N")
    n_text = (
        f"{float(n_value):.2f}"
        if n_value is not None and math.isfinite(_float(n_value))
        else str(n_shadow.get("N_status", "SHADOW_NO_DATA"))
    )
    lines = [
        f"避风港 v0.4 收盘更新｜{date}",
        (
            f"P {_fmt_score(scores, 'P')} / "
            f"S {_fmt_score(scores, 'S')} / "
            f"G {_fmt_score(scores, 'G')} / "
            f"E {_fmt_score(scores, 'E')}"
        ),
        (
            f"R {_fmt_score(scores, 'R')} "
            f"(Put {_fmt_score(scores, 'R_put')} / "
            f"Call {_fmt_score(scores, 'R_call')}) / "
            f"I {_fmt_score(scores, 'I')} / N {n_text}"
        ),
        f"状态：{state}",
    ]
    if decision.get("data_guard"):
        lines.append("动作：DATA_GUARD，全部新增交易动作暂停。")
    else:
        lines.append(
            "动作："
            f"恐慌仓 {decision['panic_reserve']}；"
            f"卖Put {decision['sell_put']}；"
            f"卖Call {decision['sell_call']}；"
            f"保险 {decision['buy_insurance']}。"
        )
    lines.extend(
        [
            "口径：收盘计算，下一交易日生效。",
            "边界：研究/Shadow only；不连接券商，不自动下单。",
        ]
    )
    return "\n".join(lines)


def close_update_once(
    *,
    now_et: datetime | None = None,
    force_window: bool = False,
) -> str:
    """Generate a close update message, or '' if outside window/holiday.

    Pure message generation — no ledger logic.
    """
    current = now_et or datetime.now(ZoneInfo("America/New_York"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("America/New_York"))
    else:
        current = current.astimezone(ZoneInfo("America/New_York"))
    inside_window = (
        current.weekday() < 5
        and time(16, 15) <= current.time().replace(tzinfo=None) <= time(17, 30)
    )
    if not force_window and not inside_window:
        return ""

    snapshot = _refresh_snapshot()
    model = dict(snapshot.get("model") or {})
    signal_date = str(model.get("signal_date") or "")
    if (
        not force_window
        and signal_date != str(current.date())
    ):
        return ""

    return format_close_message(snapshot)


# ── Two-phase delivery ledger ──────────────────────────────────────
#
# Phase 1 (PENDING): script outputs message, writes pending ledger.
# Phase 2 (CONFIRMED): next run checks Hermes last_delivery_error.
#   - None → delivery succeeded → promote pending→confirmed → silent
#   - Error string → delivery failed → retry (output again)

_PENDING_LEDGER = "close_delivery_pending.json"
_CONFIRMED_LEDGER = "close_delivery_confirmed.json"
_CRON_JOB_NAME = "Haven close update"


def _state_dir() -> Path:
    path = _hermes_home() / "data" / "haven-market-model"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cron_jobs_path() -> Path | None:
    """Return path to Hermes cron jobs.json, or None if unreachable."""
    home = Path.home() / ".hermes"
    candidate = home / "cron" / "jobs.json"
    if candidate.exists():
        return candidate
    candidate2 = home / "data" / "cron" / "jobs.json"
    if candidate2.exists():
        return candidate2
    return None


def _read_job_delivery_status() -> bool | None:
    """Check Hermes cron job delivery status.

    Returns:
        True  → last_delivery_error is None (delivery succeeded)
        False → last_delivery_error is set (delivery failed)
        None  → job not found or file not accessible
    """
    path = _cron_jobs_path()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for job in data.get("jobs", []):
            if job.get("name") == _CRON_JOB_NAME:
                err = job.get("last_delivery_error")
                return err is None
    except (OSError, ValueError):
        pass
    return None


def _write_ledger(name: str, signal_date: str) -> None:
    """Write a ledger entry for the given signal_date."""
    path = _state_dir() / name
    path.write_text(
        _dumps({"signal_date": signal_date, "written_at": datetime.now().isoformat()})
        + "\n",
        encoding="utf-8",
    )


def _read_ledger(name: str) -> str | None:
    """Read a ledger entry, returning signal_date or None."""
    path = _state_dir() / name
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("signal_date")
    except (OSError, ValueError):
        return None


def _remove_ledger(name: str) -> None:
    """Remove a ledger file if it exists."""
    path = _state_dir() / name
    if path.exists():
        path.unlink()


def _promote_pending_to_confirmed() -> None:
    """Promote the pending ledger to confirmed."""
    signal_date = _read_ledger(_PENDING_LEDGER)
    if signal_date is not None:
        _write_ledger(_CONFIRMED_LEDGER, signal_date)
        _remove_ledger(_PENDING_LEDGER)


def close_update_once_with_ledger(
    *,
    now_et: datetime | None = None,
    force_window: bool = False,
) -> str:
    """Two-phase delivery confirmation via Hermes cron job status.

    1. Confirmed ledger exists → silent (already delivered).
    2. Pending ledger exists:
       - Hermes last_delivery_error is None → promote to confirmed → silent
       - Hermes last_delivery_error is set → retry (output again)
    3. No ledger → first attempt → output message + write pending ledger.
    """
    current = now_et or datetime.now(ZoneInfo("America/New_York"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("America/New_York"))
    else:
        current = current.astimezone(ZoneInfo("America/New_York"))
    inside_window = (
        current.weekday() < 5
        and time(16, 15) <= current.time().replace(tzinfo=None) <= time(17, 30)
    )
    if not force_window and not inside_window:
        return ""

    snapshot = _refresh_snapshot()
    model = dict(snapshot.get("model") or {})
    signal_date = str(model.get("signal_date") or "")
    if (
        not force_window
        and signal_date != str(current.date())
    ):
        return ""

    # ── Check confirmed ledger (already delivered successfully) ──
    confirmed = _read_ledger(_CONFIRMED_LEDGER)
    if confirmed == signal_date:
        return ""

    # ── Check pending ledger (previous attempt may or may not have delivered) ──
    pending = _read_ledger(_PENDING_LEDGER)
    if pending == signal_date:
        # Previous run output a message. Check Hermes delivery status.
        status = _read_job_delivery_status()
        if status is True:
            # Delivery succeeded! Promote pending → confirmed, go silent.
            _promote_pending_to_confirmed()
            return ""
        # Delivery failed (or job not yet found). Retry.
        # Fall through to output again.

    # ── First attempt (no pending ledger) ──
    message = format_close_message(snapshot)
    _write_ledger(_PENDING_LEDGER, signal_date)
    return signal_date + "||" + message


def has_delivery_for_signal(signal_date: str) -> bool:
    """Check whether the confirmed delivery ledger records this signal_date."""
    return _read_ledger(_CONFIRMED_LEDGER) == signal_date


# ── Score calculation audit ────────────────────────────────────────
#

_NOMINAL_WEIGHTS: dict[str, dict[str, float]] = {
    "panic": {
        "drawdown_63": 0.12,
        "drawdown_252": 0.10,
        "below_ma200": 0.08,
        "negative_return_5": 0.05,
        "vxn_level": 0.15,
        "vxn_change_5": 0.10,
        "breadth_rel_20": 0.10,
        "breadth_rel_63": 0.10,
        "credit_stress_level": 0.05,
        "credit_widening": 0.05,
        "vix_term_stress": 0.10,
    },
    "stabilization": {
        "return_5": 0.10,
        "return_10": 0.10,
        "above_ma10": 0.075,
        "above_ma20": 0.075,
        "vxn_cooling": 0.10,
        "vxn_off_high": 0.10,
        "breadth_rel_5": 0.125,
        "breadth_rel_20": 0.125,
        "credit_narrowing": 0.10,
        "no_new_low_5": 0.10,
    },
    "greed": {
        "return_20": 0.10,
        "return_63": 0.10,
        "above_ma20": 0.10,
        "low_vxn": 0.15,
        "term_contango": 0.10,
        "above_ma200": 0.10,
        "return_252": 0.10,
        "breadth_concentration_20": 0.075,
        "breadth_concentration_63": 0.075,
        "rsi": 0.05,
        "up_day_share": 0.05,
    },
    "exhaustion": {
        "momentum_deceleration": 0.20,
        "negative_volume": 0.15,
        "distance_below_high": 0.15,
        "rsi_rollover": 0.15,
        "volatility_crash": 0.15,
        "breadth_exhaustion": 0.20,
    },
    "premium_proxy": {
        "skew_richness": 0.10,
        "vvix_richness": 0.10,
        "term_richness": 0.10,
        "iv_percentile": 0.35,
        "iv_minus_realized": 0.35,
    },
}

_SCORE_GROUP_MAP: dict[str, str] = {
    "P": "panic",
    "S": "stabilization",
    "G": "greed",
    "E": "exhaustion",
    "R": "premium_proxy",
}

_SMOOTHING_DAYS = 5


def _build_calculation_audit(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Produce a detailed audit for each score (P, S, G, E, R, I, ...).

    Returns a dict keyed by score code::

        {"P": {...}, "S": {...}, "G": {...}, "E": {...},
         "R": {...}, "I": {...}, "I_need": {...}, "I_affordability": {...}}
    """
    model = dict(snapshot.get("model") or {})
    scores = dict(model.get("scores") or {})
    score_components = dict(snapshot.get("score_components") or {})
    signal_date = str(model.get("signal_date", ""))

    audits: dict[str, Any] = {}

    # ── Composite scores (P, S, G, E) ─────────────────────────────
    model_status = str(model.get("status", "UNKNOWN"))
    for score_code, group_name in _SCORE_GROUP_MAP.items():
        if group_name == "premium_proxy":
            # Handle R separately below
            continue
        nominal = _NOMINAL_WEIGHTS.get(group_name, {})
        group_data = {}
        if group_name in score_components:
            group_data = dict(score_components[group_name])
        audits[score_code] = _audit_single_score(
            score_code=score_code,
            score_value=_float(scores.get(score_code)),
            coverage=_float(scores.get(f"{score_code}_coverage")),
            nominal_weights=nominal,
            component_values=group_data,
            signal_date=signal_date,
            status=model_status,
        )

    # ── Premium proxy (R / R_proxy, R_put, R_call) ────────────────
    premium_nominal = _NOMINAL_WEIGHTS["premium_proxy"]
    premium_group = {}
    if "premium_proxy" in score_components:
        premium_group = dict(score_components["premium_proxy"])
    # Also check enriched group for additional components
    enriched_premium = {}
    if "premium_enriched" in score_components:
        enriched_premium = dict(score_components["premium_enriched"])
    # Merge enriched into premium_group (enriched takes priority)
    for k, v in enriched_premium.items():
        if k in premium_nominal:
            premium_group[k] = v

    r_value = _float(scores.get("R"))
    r_coverage_val = _float(scores.get("R_coverage"))
    r_proxy_value = _float(scores.get("R_proxy"))
    r_proxy_coverage_val = _float(scores.get("R_proxy_coverage"))

    audits["R"] = _audit_single_score(
        score_code="R",
        score_value=r_value if math.isfinite(r_value) else r_proxy_value,
        coverage=r_coverage_val if math.isfinite(r_coverage_val) else r_proxy_coverage_val,
        nominal_weights=premium_nominal,
        component_values=premium_group,
        signal_date=signal_date,
        status=model_status,
    )
    # R_put / R_call are skew-split from R_proxy — annotate as derived
    for derived_code in ("R_put", "R_call"):
        dv = _float(scores.get(derived_code))
        dc = _float(scores.get(f"{derived_code}_coverage"))
        audits[derived_code] = {
            "score_code": derived_code,
            "value": None if not math.isfinite(dv) else dv,
            "coverage": None if not math.isfinite(dc) else dc,
            "smoothing_days": _SMOOTHING_DAYS,
            "effective_coverage_pct": (
                round(dc * 100.0, 2) if math.isfinite(dc) else None
            ),
            "components": [],
            "total_contribution": None,
            "reconstruction_error": None,
            "status": str(model.get("status", "UNKNOWN")),
            "signal_date": signal_date,
            "derived_from": "R_proxy (skew split)",
        }

    # ── Insurance scores (I, I_need, I_affordability) ─────────────
    for i_code in ("I", "I_need", "I_affordability"):
        iv = _float(scores.get(i_code))
        audits[i_code] = {
            "score_code": i_code,
            "value": None if not math.isfinite(iv) else iv,
            "coverage": None,
            "smoothing_days": _SMOOTHING_DAYS,
            "effective_coverage_pct": None,
            "components": [],
            "total_contribution": None,
            "reconstruction_error": None,
            "status": str(model.get("status", "UNKNOWN")),
            "signal_date": signal_date,
            "no_component_breakdown": True,
        }

    # ── Shadow markers ────────────────────────────────────────────
    audits["breadth_shadow"] = True
    audits["chain_shadow"] = True
    audits["N_news_shadow"] = True

    return audits


def _audit_single_score(
    *,
    score_code: str,
    score_value: float,
    coverage: float,
    nominal_weights: dict[str, float],
    component_values: dict[str, Any],
    signal_date: str,
    status: str = "UNKNOWN",
) -> dict[str, Any]:
    """Build the audit entry for one composite score."""
    components: list[dict[str, Any]] = []
    total_contribution = 0.0
    available_weight = 0.0

    # Determine which nominal weights are actually present in data
    present: dict[str, float] = {}
    for name, weight in nominal_weights.items():
        raw = component_values.get(name)
        val = _float(raw) if raw is not None else math.nan
        if math.isfinite(val):
            present[name] = weight
            available_weight += weight

    # Renormalise effective weights
    effective_weights: dict[str, float] = {}
    if available_weight > 0.0:
        scale = 1.0 / available_weight
        for name, weight in present.items():
            effective_weights[name] = weight * scale
    else:
        effective_weights = {name: 0.0 for name in present}

    # Build each component entry
    for name in present:
        raw = component_values[name]
        val = _float(raw) if raw is not None else math.nan
        nw = nominal_weights[name]
        ew = effective_weights[name]
        contrib = val * ew if math.isfinite(val) else 0.0
        components.append(
            {
                "name": name,
                "normalized_value": val,
                "nominal_weight": nw,
                "effective_weight": round(ew, 6),
                "contribution": round(contrib, 6),
            }
        )
        total_contribution += contrib

    # Reconstruct pre-smooth score
    pre_smooth = total_contribution
    # The reported score from the snapshot
    reported = score_value if math.isfinite(score_value) else None
    err = abs(reported - pre_smooth) if (reported is not None and math.isfinite(pre_smooth)) else None

    effective_cov = (
        round(coverage * 100.0, 2) if math.isfinite(coverage) else None
    )

    return {
        "score_code": score_code,
        "value": reported,
        "coverage": coverage if math.isfinite(coverage) else None,
        "smoothing_days": _SMOOTHING_DAYS,
        "effective_coverage_pct": effective_cov,
        "components": components,
        "total_contribution": round(pre_smooth, 6),
        "reconstruction_error": round(err, 6) if err is not None else None,
        "status": status,
        "signal_date": signal_date,
    }


HANDLERS = {
    "haven_model_status": handle_model_status,
    "haven_refresh_close": handle_refresh_close,
    "haven_screen_tqqq_calls": handle_screen_tqqq_calls,
    "haven_backtest_v04": handle_backtest_v04,
}
