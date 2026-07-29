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


def _decompose_blend(
    components: dict[str, tuple[pd.Series, float, pd.Series]],
) -> dict[str, dict[str, pd.Series | float]]:
    """Compute per-component effective weights matching _blend() exactly.

    Returns dict: name -> {effective_weight, contribution, raw_score, nominal_weight}.
    Each result is a Series over the same index as the inputs.

    Guarantees: for each row,
      sum(contribution_i) == _blend()[0]  (the blended score)
      sum(effective_weight_i) == 1.0       (when any data available)
    """
    if not components:
        return {}
    index = next(iter(components.values()))[0].index
    total_weight = sum(float(w) for _, w, _ in components.values())
    available = _series(index, 0.0)
    for score, weight, coverage in components.values():
        cw = float(weight)
        effective = (
            coverage.astype(float).clip(0.0, 1.0)
            * score.notna().astype(float)
            * cw
        )
        available = available.add(effective, fill_value=0.0)
    available_safe = available.replace(0.0, np.nan)

    result: dict[str, dict[str, pd.Series | float]] = {}
    for name, (score, weight, coverage) in components.items():
        cw = float(weight)
        effective = (
            coverage.astype(float).clip(0.0, 1.0)
            * score.notna().astype(float)
            * cw
        )
        eff_w = effective / available_safe
        raw_w = cw / max(total_weight, 1e-12)
        contrib = score.fillna(0.0) * eff_w
        result[name] = {
            "effective_weight": eff_w,
            "contribution": contrib,
            "raw_score": score,
            "nominal_weight": raw_w,
        }
    return result


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
) -> tuple[dict[str, pd.Series], dict[str, pd.DataFrame | dict]]:
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

    r_put_blend = {
        "atm": (atm, 0.55, atm_cov),
        "skew": (skew_richness, 0.25, skew_coverage),
        "vol_of_vol": (vvix_richness, 0.10, vvix_coverage),
        "term": (term_richness, 0.10, term_coverage),
    }
    r_put, r_put_cov = _blend(r_put_blend)

    r_call_blend = {
        "atm": (atm, 0.65, atm_cov),
        "inverse_skew": (
            100.0 - skew_richness,
            0.15,
            skew_coverage,
        ),
        "vol_of_vol": (vvix_richness, 0.10, vvix_coverage),
        "term": (term_richness, 0.10, term_coverage),
    }
    r_call, r_call_cov = _blend(r_call_blend)

    r_blend = {
        "put": (r_put, 0.50, r_put_cov),
        "call": (r_call, 0.50, r_call_cov),
    }
    r, r_cov = _blend(r_blend)

    # Decompositions — exact same inputs as _blend, for audit
    r_put_dec = _decompose_blend(r_put_blend)
    r_call_dec = _decompose_blend(r_call_blend)
    r_dec = _decompose_blend(r_blend)

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
            "r_put_decomposition": r_put_dec,
            "r_call_decomposition": r_call_dec,
            "r_decomposition": r_dec,
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
    include_audit: bool = False,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame | dict]] | tuple[pd.DataFrame, dict[str, pd.DataFrame | dict], dict[str, list[dict]]]:
    """Build the causal v0.4 scores without allowing N to alter positions."""

    disabled = set(disabled_overlays)
    base, base_components, base_audit = build_scores(data, config, include_audit=True)
    breadth, breadth_components = _breadth_satellites(data, config)
    liquidity, liquidity_components = _liquidity_satellites(data, config)
    tail, tail_components = _tail_satellites(data, base, config)
    weights = config["enriched_scores"]["weights"]
    latest_date = str(data.index[-1].date()) if len(data.index) else ""
    smooth = int(config["scores"]["smoothing_days"])

    def _build_overlay_audit(
        score_code: str,
        overlay_blend: dict[str, tuple[pd.Series, float, pd.Series]],
        final_score: pd.Series,
        final_coverage: pd.Series,
    ) -> list[dict]:
        """Build audit entries for an overlay-blended score (P/S/G/E)."""
        if data.empty:
            return []
        row_idx = data.index[-1]
        entries: list[dict] = []
        total_nominal = sum(w for _, (_, w, _) in overlay_blend.items())
        # Compute available weight at the last row
        total_available = 0.0
        for name, (score, weight, cov) in overlay_blend.items():
            val = float(score.loc[row_idx]) if pd.notna(score.loc[row_idx]) else None
            cover = float(cov.loc[row_idx]) if pd.notna(cov.loc[row_idx]) else 0.0
            if val is not None and cover > 0.0:
                total_available += weight
        if total_available == 0.0:
            total_available = total_nominal
        for name, (score, weight, cov) in overlay_blend.items():
            val = float(score.loc[row_idx]) if pd.notna(score.loc[row_idx]) else None
            cover = float(cov.loc[row_idx]) if pd.notna(cov.loc[row_idx]) else 0.0
            effective_w = weight / total_available if total_available > 0.0 else 0.0
            contrib = (val * effective_w) if (val is not None and effective_w > 0.0) else None
            source = f"base({score_code})" if name == "base" else name
            entries.append({
                "name": f"{score_code}_overlay_{name}",
                "raw_indicator": val,
                "data_date": latest_date,
                "source": source,
                "transformation": f"weighted blend (nominal {weight}/{total_nominal})",
                "normalized_value": val,
                "nominal_weight": weight,
                "effective_weight": round(effective_w, 6),
                "contribution": round(contrib, 6) if contrib is not None else None,
                "coverage": 1.0 if val is not None else 0.0,
            })
        return entries

    p_blend = {
        "base": (
            base["P"],
            float(weights["P"]["base"]),
            base["P_coverage"],
        ),
        "breadth": (
            breadth["breadth_stress"],
            _overlay_weight(weights["P"], "breadth", disabled),
            breadth["breadth_stress_coverage"],
        ),
        "liquidity": (
            liquidity["liquidity_stress"],
            _overlay_weight(weights["P"], "liquidity", disabled),
            liquidity["liquidity_stress_coverage"],
        ),
        "tail": (
            tail["tail_stress"],
            _overlay_weight(weights["P"], "tail", disabled),
            tail["tail_stress_coverage"],
        ),
    }
    p, p_cov = _blend(p_blend)

    s_blend = {
        "base": (
            base["S"],
            float(weights["S"]["base"]),
            base["S_coverage"],
        ),
        "breadth": (
            breadth["breadth_recovery"],
            _overlay_weight(weights["S"], "breadth", disabled),
            breadth["breadth_recovery_coverage"],
        ),
        "liquidity": (
            liquidity["liquidity_relief"],
            _overlay_weight(weights["S"], "liquidity", disabled),
            liquidity["liquidity_relief_coverage"],
        ),
    }
    s, s_cov = _blend(s_blend)

    g_blend = {
        "base": (
            base["G"],
            float(weights["G"]["base"]),
            base["G_coverage"],
        ),
        "breadth": (
            breadth["breadth_concentration"],
            _overlay_weight(weights["G"], "breadth", disabled),
            breadth["breadth_concentration_coverage"],
        ),
        "tail": (
            tail["tail_complacency"],
            _overlay_weight(weights["G"], "tail", disabled),
            tail["tail_stress_coverage"],
        ),
    }
    g, g_cov = _blend(g_blend)

    e_blend = {
        "base": (
            base["E"],
            float(weights["E"]["base"]),
            base["E_coverage"],
        ),
        "breadth": (
            breadth["breadth_exhaustion"],
            _overlay_weight(weights["E"], "breadth", disabled),
            breadth["breadth_exhaustion_coverage"],
        ),
        "tail": (
            tail["tail_stress"],
            _overlay_weight(weights["E"], "tail", disabled),
            tail["tail_stress_coverage"],
        ),
    }
    e, e_cov = _blend(e_blend)

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
    r = tail["R"] if "tail" not in disabled else base["R_proxy"]
    r_cov = tail["R_coverage"] if "tail" not in disabled else base["R_proxy_coverage"]
    insurance = build_insurance_scores(p, g, e, r_put)

    # ── Build enriched audit ────────────────────────────────────────
    audit: dict[str, list[dict]] = copy.deepcopy(base_audit)

    # Overlay audit for P/S/G/E
    overlay_configs = [
        ("P", p_blend, p, p_cov),
        ("S", s_blend, s, s_cov),
        ("G", g_blend, g, g_cov),
        ("E", e_blend, e, e_cov),
    ]
    for code, blend, final_s, final_cov in overlay_configs:
        overlay_key = f"{code}_overlay"
        overlay_entries = _build_overlay_audit(code, blend, final_s, final_cov)
        if overlay_entries:
            audit[overlay_key] = overlay_entries

    # R_put and R_call audit (using engine-native decomposition)
    row_idx = data.index[-1] if not data.empty else None
    if row_idx is not None:
        def _dec_entry(
            name: str,
            dec: dict,
            idx,
            date_str: str,
            nw: float,
        ) -> dict:
            eff_w = float(dec["effective_weight"].loc[idx]) if pd.notna(dec["effective_weight"].loc[idx]) else 0.0
            raw = float(dec["raw_score"].loc[idx]) if pd.notna(dec["raw_score"].loc[idx]) else None
            contrib = float(dec["contribution"].loc[idx]) if pd.notna(dec["contribution"].loc[idx]) else None
            return {
                "name": name,
                "raw_indicator": raw,
                "data_date": date_str,
                "source": name,
                "transformation": "weighted blend (decomposition from _blend)",
                "normalized_value": raw,
                "nominal_weight": nw,
                "effective_weight": round(eff_w, 6),
                "contribution": round(contrib, 6) if contrib is not None else None,
                "coverage": 1.0 if raw is not None else 0.0,
            }

        # R_put breakdown
        r_put_dec = tail_components.get("r_put_decomposition", {})
        r_put_entries = []
        for comp_name in ["atm", "skew", "vol_of_vol", "term"]:
            if comp_name in r_put_dec:
                nw = float(r_put_dec[comp_name].get("nominal_weight", 0.0))
                r_put_entries.append(_dec_entry(comp_name, r_put_dec[comp_name], row_idx, latest_date, nw))
        audit["R_put"] = r_put_entries

        # R_call breakdown
        r_call_dec = tail_components.get("r_call_decomposition", {})
        r_call_entries = []
        for comp_name in ["atm", "inverse_skew", "vol_of_vol", "term"]:
            if comp_name in r_call_dec:
                nw = float(r_call_dec[comp_name].get("nominal_weight", 0.0))
                r_call_entries.append(_dec_entry(comp_name, r_call_dec[comp_name], row_idx, latest_date, nw))
        audit["R_call"] = r_call_entries

        # R composite (put/call legs)
        r_dec = tail_components.get("r_decomposition", {})
        r_val = float(r.loc[row_idx]) if pd.notna(r.loc[row_idx]) else None
        r_entries = []
        for comp_name in ["put", "call"]:
            if comp_name in r_dec:
                nw = float(r_dec[comp_name].get("nominal_weight", 0.0))
                r_entries.append(_dec_entry(comp_name, r_dec[comp_name], row_idx, latest_date, nw))
        audit["R"] = r_entries

        # I insurance (enriched version using blended scores)
        insurance_last = insurance.iloc[-1]
        i_need_v = float(insurance_last["I_need"]) if pd.notna(insurance_last["I_need"]) else None
        afford_v = float(insurance_last["affordability"]) if pd.notna(insurance_last["affordability"]) else None
        rl_v = float(insurance_last.get("risk_level", np.nan)) if pd.notna(insurance_last.get("risk_level", np.nan)) else None
        ra_v = float(insurance_last.get("risk_acceleration", np.nan)) if pd.notna(insurance_last.get("risk_acceleration", np.nan)) else None
        fr_v = float(insurance_last.get("fragility", np.nan)) if pd.notna(insurance_last.get("fragility", np.nan)) else None

        # I = 0.75 * I_need + 0.25 * affordability
        i_avail = sum(w for v, w in [(i_need_v is not None, 0.75), (afford_v is not None, 0.25)])
        i_eff_n = 0.75 / i_avail if i_avail > 0.0 else 0.0
        i_eff_a = 0.25 / i_avail if i_avail > 0.0 else 0.0
        i_c_n = (i_need_v * i_eff_n) if i_need_v is not None else None
        i_c_a = (afford_v * i_eff_a) if afford_v is not None else None

        audit["I"] = [
            {
                "name": "I_need",
                "raw_indicator": float(insurance_last["risk_level"]) if pd.notna(insurance_last["risk_level"]) else None,
                "data_date": latest_date,
                "source": "P/G/E → risk_level/risk_acceleration/fragility composite",
                "transformation": "0.5*risk_level + 0.3*risk_acceleration + 0.2*fragility",
                "normalized_value": i_need_v,
                "nominal_weight": 0.75,
                "effective_weight": round(i_eff_n, 6),
                "contribution": round(i_c_n, 6) if i_c_n is not None else None,
                "coverage": 1.0 if i_need_v is not None else 0.0,
            },
            {
                "name": "affordability",
                "raw_indicator": afford_v,
                "data_date": latest_date,
                "source": "100 - R_put",
                "transformation": "(100 - R_put).clip(0, 100)",
                "normalized_value": afford_v,
                "nominal_weight": 0.25,
                "effective_weight": round(i_eff_a, 6),
                "contribution": round(i_c_a, 6) if i_c_a is not None else None,
                "coverage": 1.0 if afford_v is not None else 0.0,
            },
        ]

        # I_need = 0.50 * risk_level + 0.30 * risk_acceleration + 0.20 * fragility
        need_avail = sum(w for v, w in [(rl_v is not None, 0.50), (ra_v is not None, 0.30), (fr_v is not None, 0.20)])
        need_base = need_avail if need_avail > 0.0 else 1.0
        rl_eff = 0.50 / need_base; ra_eff = 0.30 / need_base; fr_eff = 0.20 / need_base
        rl_contrib = (rl_v * rl_eff) if rl_v is not None else None
        ra_contrib = (ra_v * ra_eff) if ra_v is not None else None
        fr_contrib = (fr_v * fr_eff) if fr_v is not None else None

        audit["I_need"] = [
            {
                "name": "risk_level",
                "raw_indicator": rl_v,
                "data_date": latest_date,
                "source": "panic",
                "transformation": "((P - 20) / 40 * 100).clip(0, 100)",
                "normalized_value": rl_v,
                "nominal_weight": 0.50,
                "effective_weight": round(rl_eff, 6),
                "contribution": round(rl_contrib, 6) if rl_contrib is not None else None,
                "coverage": 1.0 if rl_v is not None else 0.0,
            },
            {
                "name": "risk_acceleration",
                "raw_indicator": ra_v,
                "data_date": latest_date,
                "source": "panic",
                "transformation": "(P - P.shift(5)).clip(0) / 20 * 100",
                "normalized_value": ra_v,
                "nominal_weight": 0.30,
                "effective_weight": round(ra_eff, 6),
                "contribution": round(ra_contrib, 6) if ra_contrib is not None else None,
                "coverage": 1.0 if ra_v is not None else 0.0,
            },
            {
                "name": "fragility",
                "raw_indicator": fr_v,
                "data_date": latest_date,
                "source": "exhaustion/greed",
                "transformation": "0.6*E + 0.4*G",
                "normalized_value": fr_v,
                "nominal_weight": 0.20,
                "effective_weight": round(fr_eff, 6),
                "contribution": round(fr_contrib, 6) if fr_contrib is not None else None,
                "coverage": 1.0 if fr_v is not None else 0.0,
            },
        ]

        # I_affordability = affordability (single component)
        audit["I_affordability"] = [
            {
                "name": "affordability",
                "raw_indicator": afford_v,
                "data_date": latest_date,
                "source": "R_put",
                "transformation": "(100 - R_put).clip(0, 100)",
                "normalized_value": afford_v,
                "nominal_weight": 1.0,
                "effective_weight": 1.0,
                "contribution": afford_v if afford_v is not None else None,
                "coverage": 1.0 if afford_v is not None else 0.0,
            },
        ]

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
    if include_audit:
        return result, components, audit
    return result, components


def _build_r_option_audit(
    score_code: str,
    components: dict[str, tuple[float | None, float, float]],
    final_value: float | None,
    latest_date: str,
    smoothing_days: int,
) -> list[dict]:
    """Build audit entries for R_put or R_call."""
    entries: list[dict] = []
    total_nominal = sum(w for _, (_, w, _) in components.items())
    total_available = sum(
        w for _, (v, w, c) in components.items()
        if v is not None and c > 0.0
    )
    if total_available <= 0.0:
        total_available = total_nominal
    for name, (val, weight, cov) in components.items():
        effective_w = weight / total_available if total_available > 0.0 else 0.0
        contrib = (val * effective_w) if (val is not None and effective_w > 0.0) else None
        entries.append({
            "name": name,
            "raw_indicator": val,
            "data_date": latest_date,
            "source": name,
            "transformation": f"weighted blend (nominal {weight}/{total_nominal})",
            "normalized_value": val,
            "nominal_weight": weight,
            "effective_weight": round(effective_w, 6),
            "contribution": round(contrib, 6) if contrib is not None else None,
            "coverage": 1.0 if val is not None else 0.0,
        })
    return entries


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
