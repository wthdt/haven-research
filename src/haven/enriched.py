from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .data import fetch_cboe_history, fetch_fred_series
from .model import build_insurance_scores, build_scores, rolling_percentile


def _series(index: pd.Index, value: float = np.nan) -> pd.Series:
    return pd.Series(value, index=index, dtype=float)


def _rolling_percentile(
    values: pd.Series,
    config: dict[str, Any],
    *,
    short: bool = False,
) -> pd.Series:
    score_config = config["scores"]
    min_periods = int(
        score_config[
            "shorter_history_min_periods"
            if short
            else "percentile_min_periods"
        ]
    )
    return rolling_percentile(
        values,
        int(score_config["percentile_window"]),
        min_periods,
    )


def _stress(percentile: pd.Series) -> pd.Series:
    return ((percentile - 50.0) * 2.0).clip(0.0, 100.0)


def _composite(
    components: dict[str, tuple[pd.Series, float]],
    smoothing_days: int,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    if not components:
        raise ValueError("components cannot be empty")
    values = pd.DataFrame(
        {name: series.astype(float) for name, (series, _) in components.items()}
    )
    weights = pd.Series(
        {name: float(weight) for name, (_, weight) in components.items()}
    )
    total_weight = float(weights.sum())
    available = values.notna().mul(weights, axis=1).sum(axis=1)
    numerator = values.mul(weights, axis=1).sum(axis=1, min_count=1)
    score = numerator / available.replace(0.0, np.nan)
    if smoothing_days > 1:
        score = score.rolling(smoothing_days, min_periods=1).mean()
    coverage = available / max(total_weight, 1e-12)
    return score.clip(0.0, 100.0), coverage.clip(0.0, 1.0), values


def _blend(
    components: dict[str, tuple[pd.Series, float, pd.Series]],
) -> tuple[pd.Series, pd.Series]:
    if not components:
        raise ValueError("components cannot be empty")
    index = next(iter(components.values()))[0].index
    numerator = _series(index, 0.0)
    available = _series(index, 0.0)
    total_weight = 0.0
    for score, weight, coverage in components.values():
        component_weight = float(weight)
        total_weight += component_weight
        effective = (
            coverage.astype(float).clip(0.0, 1.0)
            * score.notna().astype(float)
            * component_weight
        )
        numerator = numerator.add(
            score.fillna(0.0) * effective, fill_value=0.0
        )
        available = available.add(effective, fill_value=0.0)
    result = numerator / available.replace(0.0, np.nan)
    return (
        result.clip(0.0, 100.0),
        (available / max(total_weight, 1e-12)).clip(0.0, 1.0),
    )


def _aligned_lagged(
    source: pd.Series,
    index: pd.DatetimeIndex,
    *,
    lag_days: int,
    fill_limit: int,
) -> pd.Series:
    aligned = source.reindex(index).ffill(limit=fill_limit)
    return aligned.shift(int(lag_days))


def attach_enriched_market_data(
    data: pd.DataFrame,
    config: dict[str, Any],
    project_root: Path,
    *,
    force: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach official, date-lagged diagnostics used by the v0.4 overlay."""

    result = data.copy()
    enriched = config["enriched_data"]
    raw_dir = project_root / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    index = pd.DatetimeIndex(result.index)
    latest_dates: dict[str, str | None] = {}

    for output_name, spec in enriched.get("fred", {}).items():
        series = fetch_fred_series(
            str(spec["series_id"]), raw_dir, force=force
        )
        result[output_name] = _aligned_lagged(
            series,
            index,
            lag_days=int(spec.get("lag_trading_days", 1)),
            fill_limit=int(spec.get("fill_limit", 7)),
        )
        latest_dates[output_name] = (
            str(series.dropna().index.max().date())
            if not series.dropna().empty
            else None
        )

    for output_name, spec in enriched.get("cboe", {}).items():
        frame = fetch_cboe_history(
            str(spec["index_name"]), raw_dir, force=force
        )
        series = frame["CLOSE"]
        result[output_name] = _aligned_lagged(
            series,
            index,
            lag_days=int(spec.get("lag_trading_days", 0)),
            fill_limit=int(spec.get("fill_limit", 3)),
        )
        latest_dates[output_name] = (
            str(series.dropna().index.max().date())
            if not series.dropna().empty
            else None
        )

    metadata = {
        "sources": {
            "fred": {
                name: spec["series_id"]
                for name, spec in enriched.get("fred", {}).items()
            },
            "cboe": {
                name: spec["index_name"]
                for name, spec in enriched.get("cboe", {}).items()
            },
        },
        "latest_dates": latest_dates,
        "causal_alignment": {
            name: int(spec.get("lag_trading_days", 1))
            for name, spec in enriched.get("fred", {}).items()
        }
        | {
            name: int(spec.get("lag_trading_days", 0))
            for name, spec in enriched.get("cboe", {}).items()
        },
        "revision_warning": (
            "NFCI history is publication-lagged but uses the latest revised "
            "vintage; it is a research diagnostic, not vintage-perfect data."
        ),
    }
    return result, metadata


def _breadth_satellites(
    data: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[str, pd.Series], dict[str, pd.DataFrame]]:
    index = data.index
    smooth = int(config["scores"]["smoothing_days"])
    breadth_column = (
        "qqew_close"
        if "qqew_close" in data
        else ("qew_close" if "qew_close" in data else None)
    )
    if breadth_column is None:
        missing = _series(index)
        outputs = {
            name: missing.copy()
            for name in [
                "breadth_stress",
                "breadth_stress_coverage",
                "breadth_recovery",
                "breadth_recovery_coverage",
                "breadth_concentration",
                "breadth_concentration_coverage",
                "breadth_exhaustion",
                "breadth_exhaustion_coverage",
            ]
        }
        return outputs, {}

    relative = data[breadth_column] / data["qqq_close"]
    rel_5 = relative.pct_change(5, fill_method=None)
    rel_20 = relative.pct_change(20, fill_method=None)
    rel_63 = relative.pct_change(63, fill_method=None)

    stress_components = {
        "qew_qqq_weak_5": (
            _stress(_rolling_percentile(-rel_5, config, short=True)),
            0.20,
        ),
        "qew_qqq_weak_20": (
            _stress(_rolling_percentile(-rel_20, config, short=True)),
            0.50,
        ),
        "qew_qqq_weak_63": (
            _stress(_rolling_percentile(-rel_63, config, short=True)),
            0.30,
        ),
    }
    recovery_components = {
        "qew_qqq_rebound_5": (
            _rolling_percentile(rel_5, config, short=True),
            0.55,
        ),
        "qew_qqq_rebound_20": (
            _rolling_percentile(rel_20, config, short=True),
            0.45,
        ),
    }
    concentration_components = {
        "concentration_20": (
            _rolling_percentile(-rel_20, config, short=True),
            0.60,
        ),
        "concentration_63": (
            _rolling_percentile(-rel_63, config, short=True),
            0.40,
        ),
    }
    exhaustion_components = {
        "breadth_rollover_5": (
            _rolling_percentile(-rel_5, config, short=True),
            0.60,
        ),
        "breadth_rollover_20": (
            _rolling_percentile(-rel_20, config, short=True),
            0.40,
        ),
    }

    stress, stress_cov, stress_values = _composite(
        stress_components, smooth
    )
    recovery, recovery_cov, recovery_values = _composite(
        recovery_components, smooth
    )
    concentration, concentration_cov, concentration_values = _composite(
        concentration_components, smooth
    )
    exhaustion, exhaustion_cov, exhaustion_values = _composite(
        exhaustion_components, smooth
    )
    return (
        {
            "breadth_stress": stress,
            "breadth_stress_coverage": stress_cov,
            "breadth_recovery": recovery,
            "breadth_recovery_coverage": recovery_cov,
            "breadth_concentration": concentration,
            "breadth_concentration_coverage": concentration_cov,
            "breadth_exhaustion": exhaustion,
            "breadth_exhaustion_coverage": exhaustion_cov,
        },
        {
            "breadth_stress": stress_values,
            "breadth_recovery": recovery_values,
            "breadth_concentration": concentration_values,
            "breadth_exhaustion": exhaustion_values,
        },
    )


def _liquidity_satellites(
    data: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[str, pd.Series], dict[str, pd.DataFrame]]:
    index = data.index
    smooth = int(config["scores"]["smoothing_days"])
    missing = _series(index)

    hy = data.get("hy_spread_enriched", missing)
    ccc = data.get("ccc_spread", missing)
    nfci = data.get("nfci", missing)
    dgs2 = data.get("dgs2", missing)
    real_10y = data.get("real_yield_10y", missing)

    stress_components = {
        "hy_oas_level": (
            _stress(_rolling_percentile(hy, config, short=True)),
            0.12,
        ),
        "hy_oas_widening_20": (
            _stress(
                _rolling_percentile(
                    hy.diff(20), config, short=True
                )
            ),
            0.12,
        ),
        "ccc_oas_level": (
            _stress(_rolling_percentile(ccc, config, short=True)),
            0.14,
        ),
        "ccc_oas_widening_20": (
            _stress(
                _rolling_percentile(
                    ccc.diff(20), config, short=True
                )
            ),
            0.14,
        ),
        "nfci_level": (
            _stress(_rolling_percentile(nfci, config)),
            0.18,
        ),
        "nfci_tightening_4w": (
            _stress(
                _rolling_percentile(
                    nfci.diff(20), config, short=True
                )
            ),
            0.10,
        ),
        "two_year_shock_5": (
            _stress(
                _rolling_percentile(
                    dgs2.diff(5), config, short=True
                )
            ),
            0.10,
        ),
        "real_yield_shock_20": (
            _stress(
                _rolling_percentile(
                    real_10y.diff(20), config, short=True
                )
            ),
            0.10,
        ),
    }
    relief_components = {
        "hy_oas_narrowing_20": (
            _rolling_percentile(-hy.diff(20), config, short=True),
            0.18,
        ),
        "ccc_oas_narrowing_20": (
            _rolling_percentile(-ccc.diff(20), config, short=True),
            0.18,
        ),
        "nfci_easing_level": (
            _rolling_percentile(-nfci, config),
            0.22,
        ),
        "nfci_easing_4w": (
            _rolling_percentile(-nfci.diff(20), config, short=True),
            0.18,
        ),
        "two_year_relief_5": (
            _rolling_percentile(-dgs2.diff(5), config, short=True),
            0.12,
        ),
        "real_yield_relief_20": (
            _rolling_percentile(-real_10y.diff(20), config, short=True),
            0.12,
        ),
    }

    stress, stress_cov, stress_values = _composite(
        stress_components, smooth
    )
    relief, relief_cov, relief_values = _composite(
        relief_components, smooth
    )
    return (
        {
            "liquidity_stress": stress,
            "liquidity_stress_coverage": stress_cov,
            "liquidity_relief": relief,
            "liquidity_relief_coverage": relief_cov,
        },
        {
            "liquidity_stress": stress_values,
            "liquidity_relief": relief_values,
        },
    )


def _tail_satellites(
    data: pd.DataFrame,
    base_scores: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[str, pd.Series], dict[str, pd.DataFrame]]:
    index = data.index
    smooth = int(config["scores"]["smoothing_days"])
    missing = _series(index)
    vvix = data.get("vvix_close", missing)
    skew = data.get("skew_close", missing)
    vix9d = data.get("vix9d_close", missing)
    vix6m = data.get("vix6m_close", missing)
    vix = data["vix_close"]
    vix3m = data["vix3m_close"]

    short_term_ratio = vix9d / vix3m
    six_month_ratio = vix / vix6m
    tail_components = {
        "vvix_level": (
            _stress(_rolling_percentile(vvix, config)),
            0.40,
        ),
        "vvix_change_5": (
            _stress(
                _rolling_percentile(
                    vvix.pct_change(5, fill_method=None),
                    config,
                    short=True,
                )
            ),
            0.20,
        ),
        "vix9d_vix3m": (
            _stress(
                _rolling_percentile(
                    short_term_ratio, config, short=True
                )
            ),
            0.25,
        ),
        "vix_vix6m": (
            _stress(
                _rolling_percentile(
                    six_month_ratio, config, short=True
                )
            ),
            0.15,
        ),
    }
    tail_stress, tail_cov, tail_values = _composite(
        tail_components, smooth
    )

    atm = base_scores["R_proxy"]
    atm_cov = base_scores["R_proxy_coverage"]
    skew_richness = _rolling_percentile(skew, config, short=True)
    skew_coverage = skew_richness.notna().astype(float)
    vvix_richness = _rolling_percentile(vvix, config, short=True)
    vvix_coverage = vvix_richness.notna().astype(float)
    term_richness = _rolling_percentile(
        short_term_ratio, config, short=True
    )
    term_coverage = term_richness.notna().astype(float)

    r_put, r_put_cov = _blend(
        {
            "atm": (atm, 0.55, atm_cov),
            "skew": (skew_richness, 0.25, skew_coverage),
            "vol_of_vol": (vvix_richness, 0.10, vvix_coverage),
            "term": (term_richness, 0.10, term_coverage),
        }
    )
    r_call, r_call_cov = _blend(
        {
            "atm": (atm, 0.65, atm_cov),
            "inverse_skew": (
                100.0 - skew_richness,
                0.15,
                skew_coverage,
            ),
            "vol_of_vol": (vvix_richness, 0.10, vvix_coverage),
            "term": (term_richness, 0.10, term_coverage),
        }
    )
    r, r_cov = _blend(
        {
            "put": (r_put, 0.50, r_put_cov),
            "call": (r_call, 0.50, r_call_cov),
        }
    )
    return (
        {
            "tail_stress": tail_stress,
            "tail_stress_coverage": tail_cov,
            "tail_complacency": 100.0 - tail_stress,
            "R": r,
            "R_coverage": r_cov,
            "R_put": r_put,
            "R_put_coverage": r_put_cov,
            "R_call": r_call,
            "R_call_coverage": r_call_cov,
            "skew_richness": skew_richness,
        },
        {
            "tail_stress": tail_values,
            "premium_enriched": pd.DataFrame(
                {
                    "atm_richness": atm,
                    "skew_richness": skew_richness,
                    "vvix_richness": vvix_richness,
                    "term_richness": term_richness,
                    "R_put": r_put,
                    "R_call": r_call,
                },
                index=index,
            ),
        },
    )


def _overlay_weight(
    weights: dict[str, float],
    name: str,
    disabled_overlays: set[str],
) -> float:
    return 0.0 if name in disabled_overlays else float(weights[name])


def build_enriched_scores(
    data: pd.DataFrame,
    config: dict[str, Any],
    *,
    disabled_overlays: Iterable[str] = (),
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Build the causal v0.4 scores without allowing N to alter positions."""

    disabled = set(disabled_overlays)
    base, base_components = build_scores(data, config)
    breadth, breadth_components = _breadth_satellites(data, config)
    liquidity, liquidity_components = _liquidity_satellites(data, config)
    tail, tail_components = _tail_satellites(data, base, config)
    weights = config["enriched_scores"]["weights"]

    p, p_cov = _blend(
        {
            "base": (
                base["P"],
                float(weights["P"]["base"]),
                base["P_coverage"],
            ),
            "breadth": (
                breadth["breadth_stress"],
                _overlay_weight(
                    weights["P"], "breadth", disabled
                ),
                breadth["breadth_stress_coverage"],
            ),
            "liquidity": (
                liquidity["liquidity_stress"],
                _overlay_weight(
                    weights["P"], "liquidity", disabled
                ),
                liquidity["liquidity_stress_coverage"],
            ),
            "tail": (
                tail["tail_stress"],
                _overlay_weight(weights["P"], "tail", disabled),
                tail["tail_stress_coverage"],
            ),
        }
    )
    s, s_cov = _blend(
        {
            "base": (
                base["S"],
                float(weights["S"]["base"]),
                base["S_coverage"],
            ),
            "breadth": (
                breadth["breadth_recovery"],
                _overlay_weight(
                    weights["S"], "breadth", disabled
                ),
                breadth["breadth_recovery_coverage"],
            ),
            "liquidity": (
                liquidity["liquidity_relief"],
                _overlay_weight(
                    weights["S"], "liquidity", disabled
                ),
                liquidity["liquidity_relief_coverage"],
            ),
        }
    )
    g, g_cov = _blend(
        {
            "base": (
                base["G"],
                float(weights["G"]["base"]),
                base["G_coverage"],
            ),
            "breadth": (
                breadth["breadth_concentration"],
                _overlay_weight(
                    weights["G"], "breadth", disabled
                ),
                breadth["breadth_concentration_coverage"],
            ),
            "tail": (
                tail["tail_complacency"],
                _overlay_weight(weights["G"], "tail", disabled),
                tail["tail_stress_coverage"],
            ),
        }
    )
    e, e_cov = _blend(
        {
            "base": (
                base["E"],
                float(weights["E"]["base"]),
                base["E_coverage"],
            ),
            "breadth": (
                breadth["breadth_exhaustion"],
                _overlay_weight(
                    weights["E"], "breadth", disabled
                ),
                breadth["breadth_exhaustion_coverage"],
            ),
            "tail": (
                tail["tail_stress"],
                _overlay_weight(weights["E"], "tail", disabled),
                tail["tail_stress_coverage"],
            ),
        }
    )

    r_put = (
        base["R_proxy"] if "tail" in disabled else tail["R_put"]
    )
    r_call = (
        base["R_proxy"] if "tail" in disabled else tail["R_call"]
    )
    r_put_cov = (
        base["R_proxy_coverage"]
        if "tail" in disabled
        else tail["R_put_coverage"]
    )
    r_call_cov = (
        base["R_proxy_coverage"]
        if "tail" in disabled
        else tail["R_call_coverage"]
    )
    r = 0.50 * r_put + 0.50 * r_call
    r_cov = 0.50 * r_put_cov + 0.50 * r_call_cov
    insurance = build_insurance_scores(p, g, e, r_put)

    result = base.copy()
    for code in ["P", "S", "G", "E"]:
        result[f"{code}_base"] = base[code]
    result["P"] = p
    result["P_coverage"] = p_cov
    result["S"] = s
    result["S_coverage"] = s_cov
    result["G"] = g
    result["G_coverage"] = g_cov
    result["E"] = e
    result["E_coverage"] = e_cov
    result["R"] = r
    result["R_coverage"] = r_cov
    result["R_put"] = r_put
    result["R_put_coverage"] = r_put_cov
    result["R_call"] = r_call
    result["R_call_coverage"] = r_call_cov
    result["R_proxy"] = r
    result["R_proxy_coverage"] = r_cov
    result["I"] = insurance["I"]
    result["I_need"] = insurance["I_need"]
    result["I_affordability"] = insurance["I_affordability"]
    result["N"] = np.nan
    result["N_coverage"] = 0.0
    result["N_affects_position"] = False
    result["option_backtest_allowed"] = False
    result["real_chain_snapshot_used_for_backtest"] = False

    for name, values in breadth.items():
        result[name] = values
    for name, values in liquidity.items():
        result[name] = values
    for name, values in tail.items():
        if name not in result:
            result[name] = values

    components = copy.deepcopy(base_components)
    components.update(breadth_components)
    components.update(liquidity_components)
    components.update(tail_components)
    components["insurance_enriched"] = insurance[
        [
            "risk_level",
            "risk_acceleration",
            "fragility",
            "affordability",
        ]
    ]
    components["overlay_scores"] = result[
        [
            "P_base",
            "P",
            "S_base",
            "S",
            "G_base",
            "G",
            "E_base",
            "E",
            "R_put",
            "R_call",
            "I",
        ]
    ]
    return result, components


def build_news_shadow_score(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Score one N snapshot. This output is intentionally non-executable."""

    fields = {
        "event_severity": 0.35,
        "x_heat": 0.20,
        "negative_sentiment": 0.20,
        "source_consensus": 0.15,
        "event_proximity": 0.10,
    }
    values: dict[str, float] = {}
    if snapshot.get("event_severity") is not None:
        values["event_severity"] = float(snapshot["event_severity"])
    if snapshot.get("x_heat") is not None:
        values["x_heat"] = float(snapshot["x_heat"])
    if snapshot.get("x_sentiment") is not None:
        sentiment = float(snapshot["x_sentiment"])
        credibility = float(snapshot.get("x_credibility", 50.0))
        values["negative_sentiment"] = (
            max(-sentiment, 0.0) * np.clip(credibility, 0.0, 100.0) / 100.0
        )
    if snapshot.get("source_consensus") is not None:
        values["source_consensus"] = float(snapshot["source_consensus"])
    if snapshot.get("event_proximity") is not None:
        values["event_proximity"] = float(snapshot["event_proximity"])

    numerator = 0.0
    available = 0.0
    for name, weight in fields.items():
        if name not in values:
            continue
        numerator += float(np.clip(values[name], 0.0, 100.0)) * weight
        available += weight
    score = numerator / available if available > 0.0 else np.nan
    if not np.isfinite(score):
        status = "SHADOW_NO_DATA"
        direction = "UNKNOWN"
    elif score >= 60.0:
        status = "SHADOW_ALERT"
        direction = "RISK_OFF"
    elif score >= 40.0:
        status = "SHADOW_WATCH"
        direction = "WATCH"
    else:
        status = "SHADOW_NORMAL"
        direction = "NEUTRAL"
    return {
        "N": float(score) if np.isfinite(score) else np.nan,
        "N_coverage": float(available),
        "N_status": status,
        "N_direction": direction,
        "N_affects_position": False,
        "components": values,
    }
