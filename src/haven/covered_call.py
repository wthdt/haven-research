from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .live_market import enrich_option_chain
from .options_proxy import _call_terms


def _finite(value: Any, default: float = np.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def covered_call_gate(
    model: dict[str, Any],
    option_config: dict[str, Any],
    *,
    shares: int,
) -> dict[str, Any]:
    """Evaluate the v0.4 call gate without creating an executable order."""

    scores = dict(model.get("scores") or {})
    state = str(model.get("status") or "UNKNOWN")
    required = {
        name: _finite(scores.get(name))
        for name in ["G", "E", "R_call"]
    }
    coverage = {
        name: _finite(scores.get(f"{name}_coverage"), 0.0)
        for name in ["G", "E", "R_call"]
    }
    if (
        "DATA_GUARD" in state
        or any(not np.isfinite(value) for value in required.values())
        or min(coverage.values(), default=0.0) < 0.80
    ):
        return {
            "status": "DATA_GUARD",
            "eligible": False,
            "reason": (
                "G/E/R_call is missing or below the 80% coverage floor"
            ),
            "automation_allowed": False,
            "maximum_researched_contracts": 0,
        }

    decision = pd.Series(
        {
            "state": state,
            "G": required["G"],
            "E": required["E"],
            "R_call": required["R_call"],
            "R_proxy": _finite(
                scores.get("R"),
                required["R_call"],
            ),
        }
    )
    terms = _call_terms(decision, option_config)
    if terms is None:
        call_config = option_config["covered_call"]
        if state == "RANGE_CARRY":
            reason = (
                "RANGE_CARRY requires "
                f"G≥{call_config['range_g_min']}, "
                f"E≥{call_config['range_e_min']}, and "
                f"R_call≥{call_config['range_r_min']}"
            )
        elif state == "GREED_TREND":
            reason = (
                "GREED_TREND requires "
                f"G≥{call_config['greed_trend_g_min']} and "
                f"R_call≥{call_config['greed_trend_r_min']}"
            )
        elif state == "GREED_EXHAUSTING":
            reason = (
                "GREED_EXHAUSTING requires "
                f"R_call≥{call_config['exhausting_r_min']}"
            )
        else:
            reason = f"State {state} is not a covered-call entry state"
        return {
            "status": "WAIT",
            "eligible": False,
            "reason": reason,
            "automation_allowed": False,
            "maximum_researched_contracts": 0,
        }

    max_coverage, target_delta, minimum_upside_buffer = terms
    researched_contracts = int(
        math.floor(max(int(shares), 0) * max_coverage / 100.0)
    )
    if researched_contracts < 1:
        return {
            "status": "CONTRACT_GRANULARITY_GUARD",
            "eligible": False,
            "reason": (
                f"{shares} shares allow only whole contracts; one contract "
                f"would cover 100 shares, above the researched "
                f"{max_coverage:.0%} cap"
            ),
            "automation_allowed": False,
            "maximum_researched_contracts": 0,
            "maximum_researched_coverage": float(max_coverage),
            "target_delta": float(target_delta),
            "minimum_upside_buffer": float(minimum_upside_buffer),
        }

    return {
        "status": "RESEARCH_GATE_OPEN",
        "eligible": True,
        "reason": "The v0.4 G/E/R_call joint gate is satisfied",
        "automation_allowed": False,
        "maximum_researched_contracts": researched_contracts,
        "maximum_researched_coverage": float(max_coverage),
        "target_delta": float(target_delta),
        "minimum_upside_buffer": float(minimum_upside_buffer),
    }


def screen_covered_calls(
    chain: pd.DataFrame,
    *,
    model: dict[str, Any],
    option_config: dict[str, Any],
    shares: int,
    rate: float,
    dividend_yield: float = 0.0,
    cost_basis: float | None = None,
    historical_premium: float = 0.0,
    target_dte: int | None = None,
    minimum_dte: int | None = None,
    maximum_dte: int | None = None,
    maximum_candidates: int = 5,
) -> dict[str, Any]:
    """Rank delayed covered-call quotes while preserving the research gate."""

    call_config = option_config["covered_call"]
    gate = covered_call_gate(model, option_config, shares=shares)
    enriched = enrich_option_chain(
        chain,
        rate=float(rate),
        dividend_yield=float(dividend_yield),
    )
    if enriched.empty:
        return {
            "status": "LIVE_CHAIN_DATA_GUARD",
            "gate": gate,
            "candidates": [],
            "research_only": True,
            "automation_allowed": False,
        }

    spot = _finite(enriched["spot"].dropna().iloc[0])
    if not np.isfinite(spot) or spot <= 0.0:
        return {
            "status": "LIVE_CHAIN_DATA_GUARD",
            "gate": gate,
            "reason": "Option chain has no valid spot price",
            "candidates": [],
            "research_only": True,
            "automation_allowed": False,
        }

    configured_target_dte = int(call_config["target_calendar_dte"])
    target_dte = int(target_dte or configured_target_dte)
    minimum_dte = int(
        minimum_dte or call_config["minimum_calendar_dte"]
    )
    maximum_dte = int(
        maximum_dte or call_config["maximum_calendar_dte"]
    )
    target_delta = _finite(gate.get("target_delta"), 0.15)
    minimum_buffer = _finite(
        gate.get("minimum_upside_buffer"),
        0.05,
    )
    commission = _finite(
        option_config.get("commission_per_contract_per_leg"),
        0.65,
    )

    calls = enriched[
        enriched["option_type"].eq("call")
        & enriched["dte"].between(minimum_dte, maximum_dte)
        & enriched["strike"].ge(spot * (1.0 + minimum_buffer))
        & enriched["delta"].between(0.05, 0.35)
        & enriched["bid"].gt(0.0)
        & enriched["ask"].ge(enriched["bid"])
    ].copy()
    if calls.empty:
        return {
            "status": "NO_LIQUID_CANDIDATE",
            "gate": gate,
            "spot": spot,
            "as_of": str(
                pd.to_datetime(enriched["as_of"].iloc[0]).date()
            ),
            "candidates": [],
            "research_only": True,
            "automation_allowed": False,
        }

    calls["delta_error"] = (calls["delta"] - target_delta).abs()
    calls["dte_error"] = (
        calls["dte"].astype(float) - float(target_dte)
    ).abs() / max(float(target_dte), 1.0)
    calls["spread_penalty"] = (
        calls["relative_spread"].fillna(1.0).clip(0.0, 1.0)
    )
    calls["liquidity_score"] = (
        np.log1p(calls["open_interest"].fillna(0.0).clip(lower=0.0))
        + 0.5
        * np.log1p(calls["volume"].fillna(0.0).clip(lower=0.0))
    )
    calls["rank_score"] = (
        100.0
        - 220.0 * calls["delta_error"]
        - 18.0 * calls["dte_error"]
        - 30.0 * calls["spread_penalty"]
        + 2.0 * calls["liquidity_score"].clip(upper=10.0)
    ).clip(0.0, 100.0)
    calls = calls.sort_values(
        [
            "rank_score",
            "open_interest",
            "volume",
            "bid",
        ],
        ascending=[False, False, False, False],
    ).head(max(1, int(maximum_candidates)))

    candidates: list[dict[str, Any]] = []
    historical_per_share = float(historical_premium) / max(
        int(shares),
        1,
    )
    for row in calls.itertuples(index=False):
        net_credit = max(float(row.bid) * 100.0 - commission, 0.0)
        premium_per_share = net_credit / 100.0
        effective_exit = float(row.strike) + premium_per_share
        candidate = {
            "expiry": str(pd.Timestamp(row.expiry).date()),
            "dte": int(row.dte),
            "strike": float(row.strike),
            "bid": float(row.bid),
            "ask": float(row.ask),
            "seller_net_credit": net_credit,
            "delta": float(row.delta),
            "approx_assignment_probability": float(row.delta),
            "open_interest": int(_finite(row.open_interest, 0.0)),
            "volume": int(_finite(row.volume, 0.0)),
            "relative_bid_ask_spread": _finite(
                row.relative_spread
            ),
            "upside_to_strike": float(row.strike) / spot - 1.0,
            "premium_yield_on_spot": premium_per_share / spot,
            "effective_exit_price": effective_exit,
            "effective_exit_with_historical_premium": (
                effective_exit + historical_per_share
            ),
            "rank_score": float(row.rank_score),
        }
        if cost_basis is not None and np.isfinite(float(cost_basis)):
            basis = float(cost_basis)
            candidate["profit_at_assignment_vs_cost"] = (
                effective_exit - basis
            ) * 100.0
            candidate[
                "profit_at_assignment_vs_cost_with_historical_premium"
            ] = (
                effective_exit + historical_per_share - basis
            ) * 100.0
        candidates.append(candidate)

    return {
        "status": (
            "CANDIDATES_RESEARCH_ONLY"
            if candidates and gate.get("eligible")
            else (
                str(gate.get("status") or "WAIT")
                if candidates
                else "NO_LIQUID_CANDIDATE"
            )
        ),
        "symbol": str(enriched.get("symbol", pd.Series(["TQQQ"])).iloc[0])
        if "symbol" in enriched
        else "TQQQ",
        "as_of": str(pd.to_datetime(enriched["as_of"].iloc[0]).date()),
        "spot": spot,
        "shares": int(shares),
        "whole_covered_contracts": max(int(shares), 0) // 100,
        "gate": gate,
        "ranking_basis": (
            "seller bid, target delta/DTE, bid-ask spread, and liquidity"
        ),
        "candidates": candidates,
        "candidate_role": (
            "eligible_research_set"
            if gate.get("eligible")
            else "contingency_watchlist_only"
        ),
        "research_only": True,
        "automation_allowed": False,
        "warning": (
            "Covered Call did not show positive net evidence in v0.4; "
            "candidates are diagnostics, never executable orders."
        ),
    }
