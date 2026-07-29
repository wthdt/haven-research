from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def rolling_percentile(
    series: pd.Series,
    window: int,
    min_periods: int,
) -> pd.Series:
    """仅使用当日及历史值计算当前值的滚动百分位。"""

    def rank_last(values: np.ndarray) -> float:
        valid = values[np.isfinite(values)]
        if valid.size == 0:
            return np.nan
        last = valid[-1]
        below = np.count_nonzero(valid < last)
        equal = np.count_nonzero(valid == last)
        return 100.0 * (below + 0.5 * equal) / valid.size

    result = series.astype(float).rolling(
        window=window, min_periods=min_periods
    ).apply(rank_last, raw=True)
    return result.clip(1.0, 99.0)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0.0)
    loss = -change.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    average_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    relative_strength = average_gain / average_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + relative_strength)
    return rsi.fillna(50.0)


def _adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=close.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=close.index,
    )
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = (
        100.0
        * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean()
        / atr.replace(0.0, np.nan)
    )
    minus_di = (
        100.0
        * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean()
        / atr.replace(0.0, np.nan)
    )
    dx = 100.0 * (plus_di - minus_di).abs() / (
        plus_di + minus_di
    ).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def _weighted_score(
    components: dict[str, tuple[pd.Series, float]],
    smoothing_days: int,
) -> tuple[pd.Series, pd.Series, pd.DataFrame, pd.Series]:
    values = pd.DataFrame({name: value for name, (value, _) in components.items()})
    weights = pd.Series({name: weight for name, (_, weight) in components.items()})
    available_weight = values.notna().mul(weights, axis=1).sum(axis=1)
    numerator = values.mul(weights, axis=1).sum(axis=1, min_count=1)
    raw_score = numerator / available_weight.replace(0.0, np.nan)
    score = raw_score.rolling(smoothing_days, min_periods=1).mean()
    return score.clip(0.0, 100.0), available_weight, values, raw_score


def _pct(
    series: pd.Series,
    config: dict[str, Any],
    short: bool = False,
) -> pd.Series:
    score_config = config["scores"]
    minimum = (
        score_config["shorter_history_min_periods"]
        if short
        else score_config["percentile_min_periods"]
    )
    return rolling_percentile(
        series,
        int(score_config["percentile_window"]),
        int(minimum),
    )


def _stress_score(percentile: pd.Series) -> pd.Series:
    """中性百分位不等于恐慌；只把上半区映射为 0～100 压力。"""
    return ((percentile - 50.0) * 2.0).clip(lower=0.0, upper=100.0)


def build_insurance_scores(
    panic: pd.Series,
    greed: pd.Series,
    exhaustion: pd.Series,
    premium_proxy: pd.Series,
) -> pd.DataFrame:
    """Build the causal I-model outputs from already-causal core scores."""

    panic_rise_5 = panic - panic.shift(5)
    risk_level = ((panic - 20.0) / 40.0 * 100.0).clip(0.0, 100.0)
    risk_acceleration = (
        panic_rise_5.clip(lower=0.0) / 20.0 * 100.0
    ).clip(0.0, 100.0)
    fragility = (0.60 * exhaustion + 0.40 * greed).clip(0.0, 100.0)
    affordability = (100.0 - premium_proxy).clip(0.0, 100.0)
    need = (
        0.50 * risk_level
        + 0.30 * risk_acceleration
        + 0.20 * fragility
    ).clip(0.0, 100.0)
    score = (0.75 * need + 0.25 * affordability).clip(0.0, 100.0)
    return pd.DataFrame(
        {
            "I": score,
            "I_need": need,
            "I_affordability": affordability,
            "risk_level": risk_level,
            "risk_acceleration": risk_acceleration,
            "fragility": fragility,
            "affordability": affordability,
        },
        index=panic.index,
    )


def build_scores(
    data: pd.DataFrame,
    config: dict[str, Any],
    *,
    include_audit: bool = False,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]] | tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, list[dict]]]:
    score_config = config["scores"]
    smooth = int(score_config["smoothing_days"])
    close = data["ndx_close"].astype(float)
    qqq = data["qqq_close"].combine_first(close)
    rsp = data["rsp_close"]
    spy = data["spy_close"]
    vxn = data["vxn_close"]
    vix = data["vix_close"]
    vix3m = data["vix3m_close"]
    credit_proxy = data["credit_proxy"]

    returns_1 = close.pct_change()
    returns_5 = close.pct_change(5)
    returns_10 = close.pct_change(10)
    returns_20 = close.pct_change(20)
    returns_63 = close.pct_change(63)
    returns_252 = close.pct_change(252)
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    drawdown_63 = 1.0 - close / close.rolling(63).max()
    drawdown_252 = 1.0 - close / close.rolling(252).max()
    below_ma200 = (1.0 - close / ma200).clip(lower=0.0)
    above_ma20 = close / ma20 - 1.0
    above_ma50 = close / ma50 - 1.0
    above_ma200 = close / ma200 - 1.0

    breadth_relative = rsp / spy
    breadth_rel_5 = breadth_relative.pct_change(5, fill_method=None)
    breadth_rel_20 = breadth_relative.pct_change(20, fill_method=None)
    breadth_rel_63 = breadth_relative.pct_change(63, fill_method=None)
    qqq_spy_relative = qqq / spy
    qqq_spy_rel_20 = qqq_spy_relative.pct_change(20, fill_method=None)

    vxn_change_5 = vxn.pct_change(5)
    vxn_off_high_20 = 1.0 - vxn / vxn.rolling(20).max()
    vix_term_ratio = vix / vix3m
    credit_change_20 = credit_proxy.pct_change(20, fill_method=None)
    credit_stress_level = (
        1.0 - credit_proxy / credit_proxy.rolling(252).max()
    )

    # ── Component metadata for audit building ──────────────────────
    # Maps (group, component_name) -> (source, transformation)
    _AUDIT_SRC: dict[str, dict[str, tuple[str, str]]] = {
        "panic": {
            "drawdown_63": ("ndx_close", "pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "drawdown_252": ("ndx_close", "pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "below_ma200": ("ndx_close", "pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "negative_return_5": ("ndx_close", "pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "vxn_level": ("vxn_close", "pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "vxn_change_5": ("vxn_close", "pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "breadth_rel_20": ("rsp_close/spy_close", "short pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "breadth_rel_63": ("rsp_close/spy_close", "short pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "credit_stress_level": ("credit_proxy", "short pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "credit_widening": ("credit_proxy", "short pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
            "vix_term_stress": ("vix_close/vix3m_close", "pct_rank(1260d) → stress_score (clip 0-100, map 50-100→0-100)"),
        },
        "stabilization": {
            "return_5": ("ndx_close", "pct_rank(1260d)"),
            "return_10": ("ndx_close", "pct_rank(1260d)"),
            "above_ma10": ("ndx_close", "pct_rank(1260d)"),
            "above_ma20": ("ndx_close", "pct_rank(1260d)"),
            "vxn_cooling": ("vxn_close", "pct_rank(1260d)"),
            "vxn_off_high": ("vxn_close", "pct_rank(1260d)"),
            "breadth_rel_5": ("rsp_close/spy_close", "short pct_rank(1260d)"),
            "breadth_rel_20": ("rsp_close/spy_close", "short pct_rank(1260d)"),
            "credit_narrowing": ("credit_proxy", "short pct_rank(1260d)"),
            "no_new_low_5": ("ndx_close", "raw bool → 0/100"),
        },
        "greed": {
            "return_20": ("ndx_close", "pct_rank(1260d)"),
            "return_63": ("ndx_close", "pct_rank(1260d)"),
            "above_ma20": ("ndx_close", "pct_rank(1260d)"),
            "low_vxn": ("vxn_close", "pct_rank(1260d)"),
            "term_contango": ("vix_close/vix3m_close", "pct_rank(1260d)"),
            "above_ma200": ("ndx_close", "pct_rank(1260d)"),
            "return_252": ("ndx_close", "pct_rank(1260d)"),
            "breadth_concentration_20": ("rsp_close/spy_close", "short pct_rank(1260d)"),
            "breadth_concentration_63": ("rsp_close/spy_close", "short pct_rank(1260d)"),
            "rsi": ("ndx_close", "pct_rank(1260d)"),
            "up_day_share": ("ndx_close", "pct_rank(1260d)"),
        },
        "exhaustion": {
            "momentum_deceleration": ("ndx_close", "pct_rank(1260d)"),
            "macd_rollover": ("ndx_close", "pct_rank(1260d)"),
            "breadth_weakening_5": ("rsp_close/spy_close", "short pct_rank(1260d)"),
            "breadth_weakening_20": ("rsp_close/spy_close", "short pct_rank(1260d)"),
            "failed_breakout": ("ndx_close", "pct_rank(1260d)"),
            "volume_price_divergence": ("qqq_close/qqq_volume", "short pct_rank(1260d)"),
            "relative_strength_weakening": ("qqq_close/spy_close", "short pct_rank(1260d)"),
            "rsi_rollover": ("ndx_close", "pct_rank(1260d)"),
        },
        "premium_proxy": {
            "iv_percentile": ("vxn_close", "pct_rank(1260d)"),
            "iv_minus_realized": ("vxn_close", "pct_rank(1260d)"),
        },
    }

    latest_date = str(data.index[-1].date()) if len(data.index) else ""

    panic_components = {
        "drawdown_63": (_stress_score(_pct(drawdown_63, config)), 0.12),
        "drawdown_252": (_stress_score(_pct(drawdown_252, config)), 0.10),
        "below_ma200": (_stress_score(_pct(below_ma200, config)), 0.08),
        "negative_return_5": (_stress_score(_pct(-returns_5, config)), 0.05),
        "vxn_level": (_stress_score(_pct(vxn, config)), 0.15),
        "vxn_change_5": (_stress_score(_pct(vxn_change_5, config)), 0.10),
        "breadth_rel_20": (
            _stress_score(_pct(-breadth_rel_20, config, short=True)),
            0.10,
        ),
        "breadth_rel_63": (
            _stress_score(_pct(-breadth_rel_63, config, short=True)),
            0.10,
        ),
        "credit_stress_level": (
            _stress_score(_pct(credit_stress_level, config, short=True)),
            0.05,
        ),
        "credit_widening": (
            _stress_score(_pct(-credit_change_20, config, short=True)),
            0.05,
        ),
        "vix_term_stress": (
            _stress_score(_pct(vix_term_ratio, config)),
            0.10,
        ),
    }
    panic, panic_coverage, panic_values, panic_raw = _weighted_score(
        panic_components, smooth
    )
    price_components = {
        key: value
        for key, value in panic_components.items()
        if key
        in {
            "drawdown_63",
            "drawdown_252",
            "below_ma200",
            "negative_return_5",
        }
    }
    price_score, price_coverage, price_values, price_raw = _weighted_score(
        price_components, smooth
    )

    no_new_low_5 = (
        close > close.rolling(5).min().shift(1)
    ).astype(float) * 100.0
    stabilization_components = {
        "return_5": (_pct(returns_5, config), 0.10),
        "return_10": (_pct(returns_10, config), 0.10),
        "above_ma10": (_pct(close / ma10 - 1.0, config), 0.075),
        "above_ma20": (_pct(above_ma20, config), 0.075),
        "vxn_cooling": (_pct(-vxn_change_5, config), 0.10),
        "vxn_off_high": (_pct(vxn_off_high_20, config), 0.10),
        "breadth_rel_5": (
            _pct(breadth_rel_5, config, short=True),
            0.125,
        ),
        "breadth_rel_20": (
            _pct(breadth_rel_20, config, short=True),
            0.125,
        ),
        "credit_narrowing": (
            _pct(credit_change_20, config, short=True),
            0.10,
        ),
        "no_new_low_5": (no_new_low_5, 0.10),
    }
    stabilization, stabilization_coverage, stabilization_values, stabilization_raw = (
        _weighted_score(stabilization_components, smooth)
    )

    rsi = _rsi(close)
    up_day_share_10 = (returns_1 > 0.0).astype(float).rolling(10).mean()
    greed_components = {
        "return_20": (_pct(returns_20, config), 0.10),
        "return_63": (_pct(returns_63, config), 0.10),
        "above_ma20": (_pct(above_ma20, config), 0.10),
        "low_vxn": (_pct(-vxn, config), 0.15),
        "term_contango": (_pct(-vix_term_ratio, config), 0.10),
        "above_ma200": (_pct(above_ma200, config), 0.10),
        "return_252": (_pct(returns_252, config), 0.10),
        "breadth_concentration_20": (
            _pct(-breadth_rel_20, config, short=True),
            0.075,
        ),
        "breadth_concentration_63": (
            _pct(-breadth_rel_63, config, short=True),
            0.075,
        ),
        "rsi": (_pct(rsi, config), 0.05),
        "up_day_share": (_pct(up_day_share_10, config), 0.05),
    }
    greed, greed_coverage, greed_values, greed_raw = _weighted_score(
        greed_components, smooth
    )

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    volume_ratio = data["qqq_volume"] / data["qqq_volume"].rolling(20).mean()
    negative_volume_day = (-data["qqq_price_return"]).clip(lower=0.0) * volume_ratio
    distance_below_high = 1.0 - close / close.rolling(20).max()
    rsi_drop = rsi.rolling(10).max() - rsi
    momentum_deceleration = returns_20 - 4.0 * returns_5
    exhaustion_components = {
        "momentum_deceleration": (
            _pct(momentum_deceleration, config),
            0.20,
        ),
        "macd_rollover": (_pct(-macd.diff(5), config), 0.15),
        "breadth_weakening_5": (
            _pct(-breadth_rel_5, config, short=True),
            0.125,
        ),
        "breadth_weakening_20": (
            _pct(-breadth_rel_20, config, short=True),
            0.125,
        ),
        "failed_breakout": (_pct(distance_below_high, config), 0.20),
        "volume_price_divergence": (
            _pct(negative_volume_day, config, short=True),
            0.10,
        ),
        "relative_strength_weakening": (
            _pct(-qqq_spy_rel_20, config, short=True),
            0.05,
        ),
        "rsi_rollover": (_pct(rsi_drop, config), 0.05),
    }
    exhaustion, exhaustion_coverage, exhaustion_values, exhaustion_raw = _weighted_score(
        exhaustion_components, smooth
    )

    realized_vol_20 = returns_1.rolling(20).std() * np.sqrt(252.0) * 100.0
    iv_minus_rv = vxn - realized_vol_20
    premium_components = {
        "iv_percentile": (_pct(vxn, config), 0.35),
        "iv_minus_realized": (_pct(iv_minus_rv, config), 0.35),
    }
    premium_proxy, premium_coverage, premium_values, premium_raw = _weighted_score(
        premium_components, smooth
    )
    # 缺少历史执行价、偏斜、买卖价差、成交量和持仓量，最高覆盖率只有 70%。
    premium_coverage = premium_coverage.clip(upper=0.70)

    # I 保险模型不是另一个方向预测器。它把“保险需求”和“当前价格是否
    # 值得买”分开，避免在风险已经爆发、隐波最贵时仅凭 P 高追买保护。
    #
    # I_need：账户持有风险资产时，对尾部保护的需求强度；
    # I_affordability：以 R_proxy 的反向值近似保险可负担性；
    # I：用于候选保险排序的综合分数。实际开仓仍受账户风险缺口、
    #     年度保费预算、整数合约和状态机共同约束。
    insurance_outputs = build_insurance_scores(
        panic,
        greed,
        exhaustion,
        premium_proxy,
    )
    insurance_score = insurance_outputs["I"]
    insurance_need = insurance_outputs["I_need"]
    insurance_affordability = insurance_outputs["I_affordability"]
    insurance_values = insurance_outputs[
        [
            "risk_level",
            "risk_acceleration",
            "fragility",
            "affordability",
        ]
    ]

    qqq_high = data["qqq_high"]
    qqq_low = data["qqq_low"]
    qqq_close = data["qqq_close"]
    adx14 = _adx(qqq_high, qqq_low, qqq_close)
    qqq_ma20 = qqq_close.rolling(20).mean()
    side = np.sign(qqq_close - qqq_ma20)
    ma20_crossings = side.ne(side.shift(1)).astype(float).rolling(20).sum()
    qqq_rv20 = data["qqq_price_return"].rolling(20).std() * np.sqrt(252.0)
    qqq_rv_percentile = _pct(qqq_rv20, config, short=True)
    range_condition = (
        (data["qqq_close"].pct_change(20, fill_method=None).abs() <= 0.05)
        & ((adx14 < 20.0) | (ma20_crossings >= 4.0))
        & (qqq_rv_percentile <= 70.0)
    )

    result = pd.DataFrame(index=data.index)
    result["P"] = panic
    result["P_coverage"] = panic_coverage
    result["P_price"] = price_score
    result["P_price_coverage"] = price_coverage
    result["S"] = stabilization
    result["S_coverage"] = stabilization_coverage
    result["G"] = greed
    result["G_coverage"] = greed_coverage
    result["E"] = exhaustion
    result["E_coverage"] = exhaustion_coverage
    result["R_proxy"] = premium_proxy
    result["R_proxy_coverage"] = premium_coverage
    result["I"] = insurance_score
    result["I_need"] = insurance_need
    result["I_affordability"] = insurance_affordability
    result["option_backtest_allowed"] = False
    result["signal_close"] = close
    result["ma20"] = ma20
    result["adx14"] = adx14
    result["realized_vol_20"] = realized_vol_20
    result["range_condition"] = range_condition.fillna(False)
    result["no_new_low_5"] = no_new_low_5

    components = {
        "panic": panic_values,
        "panic_price": price_values,
        "stabilization": stabilization_values,
        "greed": greed_values,
        "exhaustion": exhaustion_values,
        "premium_proxy": premium_values,
        "insurance": insurance_values,
    }

    # ── Build calculation audit ────────────────────────────────────
    # Gather all score components by looking at which (name, weight) pairs
    # belong to each group — we precompute nominal weight lookups.
    _comp_groups: dict[str, dict[str, float]] = {
        "panic": {n: w for n, (_, w) in panic_components.items()},
        "stabilization": {n: w for n, (_, w) in stabilization_components.items()},
        "greed": {n: w for n, (_, w) in greed_components.items()},
        "exhaustion": {n: w for n, (_, w) in exhaustion_components.items()},
        "premium_proxy": {n: w for n, (_, w) in premium_components.items()},
    }

    def _build_group_audit(
        group_name: str,
        values_df: pd.DataFrame,
        raw_score_series: pd.Series,
        coverage_series: pd.Series,
    ) -> list[dict]:
        meta = _AUDIT_SRC.get(group_name, {})
        if values_df.empty:
            return []
        last_row = values_df.iloc[-1]
        weights_map = _comp_groups.get(group_name, {})
        total_available = sum(
            weights_map[c]
            for c in values_df.columns
            if c in weights_map and pd.notna(last_row.get(c))
        )
        entries: list[dict] = []
        for col in values_df.columns:
            if col not in meta:
                continue
            source, transformation = meta[col]
            norm_val = float(last_row[col]) if pd.notna(last_row[col]) else None
            nw = weights_map.get(col, 0.0)
            effective_w = nw / total_available if total_available > 0.0 else 0.0
            contrib = (norm_val * effective_w) if (norm_val is not None and effective_w > 0.0) else None
            entries.append({
                "name": col,
                "raw_indicator": norm_val,
                "data_date": latest_date,
                "source": source,
                "transformation": transformation,
                "normalized_value": norm_val,
                "nominal_weight": nw,
                "effective_weight": round(effective_w, 6),
                "contribution": round(contrib, 6) if contrib is not None else None,
                "coverage": 1.0 if norm_val is not None else 0.0,
            })
        return entries

    audit: dict[str, list[dict]] = {}

    group_configs = [
        ("P", "panic", panic_values, panic_raw, panic_coverage),
        ("S", "stabilization", stabilization_values, stabilization_raw, stabilization_coverage),
        ("G", "greed", greed_values, greed_raw, greed_coverage),
        ("E", "exhaustion", exhaustion_values, exhaustion_raw, exhaustion_coverage),
        ("R_proxy", "premium_proxy", premium_values, premium_raw, premium_coverage),
    ]
    for code, group, v_df, r_s, cov_s in group_configs:
        entries = _build_group_audit(group, v_df, r_s, cov_s)
        if entries and not data.empty:
            # Add pre-smooth raw score and smoothing window info
            smooth = int(score_config["smoothing_days"])
            raw_val = float(r_s.iloc[-1]) if pd.notna(r_s.iloc[-1]) else None
            final_val = float(result[code].iloc[-1]) if code in result.columns and pd.notna(result[code].iloc[-1]) else None
            smooth_window = []
            for i in range(min(smooth, len(r_s))):
                idx = -smooth + i
                if idx < 0:
                    idx_val = float(r_s.iloc[idx]) if pd.notna(r_s.iloc[idx]) else None
                    smooth_window.append({
                        "date": str(data.index[idx].date()),
                        "raw_score": round(idx_val, 6) if idx_val is not None else None,
                    })
            entries.append({
                "name": f"base_score",
                "raw_indicator": raw_val,
                "data_date": latest_date,
                "source": group,
                "transformation": f"sma({smooth}) of raw_score",
                "normalized_value": final_val,
                "nominal_weight": 1.0,
                "effective_weight": 1.0,
                "contribution": final_val,
                "coverage": 1.0,
                "pre_smooth_raw": round(raw_val, 6) if raw_val is not None else None,
                "smoothing_window": smooth_window,
            })
        if entries:
            audit[code] = entries

    # Insurance audit — breakdown from build_insurance_scores
    insurance_last = insurance_outputs.iloc[-1]
    i_need_v = float(insurance_last["I_need"]) if pd.notna(insurance_last["I_need"]) else None
    afford_v = float(insurance_last["affordability"]) if pd.notna(insurance_last["affordability"]) else None
    rl_v = float(insurance_last["risk_level"]) if pd.notna(insurance_last["risk_level"]) else None
    ra_v = float(insurance_last["risk_acceleration"]) if pd.notna(insurance_last["risk_acceleration"]) else None
    fr_v = float(insurance_last["fragility"]) if pd.notna(insurance_last["fragility"]) else None

    # I = 0.75 * I_need + 0.25 * affordability
    i_available = sum(w for v, w in [(i_need_v is not None, 0.75), (afford_v is not None, 0.25)])
    i_eff_need = 0.75 / i_available if i_available > 0.0 else 0.0
    i_eff_aff = 0.25 / i_available if i_available > 0.0 else 0.0
    i_contrib_need = (i_need_v * i_eff_need) if i_need_v is not None else None
    i_contrib_aff = (afford_v * i_eff_aff) if afford_v is not None else None

    audit["I"] = [
        {
            "name": "I_need",
            "raw_indicator": float(insurance_last["risk_level"]) if pd.notna(insurance_last["risk_level"]) else None,
            "data_date": latest_date,
            "source": "P/G/E composite → risk_level/risk_acceleration/fragility",
            "transformation": "0.5*risk_level + 0.3*risk_acceleration + 0.2*fragility",
            "normalized_value": i_need_v,
            "nominal_weight": 0.75,
            "effective_weight": round(i_eff_need, 6),
            "contribution": round(i_contrib_need, 6) if i_contrib_need is not None else None,
            "coverage": 1.0 if i_need_v is not None else 0.0,
        },
        {
            "name": "affordability",
            "raw_indicator": float(insurance_last["affordability"]) if pd.notna(insurance_last["affordability"]) else None,
            "data_date": latest_date,
            "source": "100 - premium_proxy",
            "transformation": "(100 - R_proxy).clip(0, 100)",
            "normalized_value": afford_v,
            "nominal_weight": 0.25,
            "effective_weight": round(i_eff_aff, 6),
            "contribution": round(i_contrib_aff, 6) if i_contrib_aff is not None else None,
            "coverage": 1.0 if afford_v is not None else 0.0,
        },
    ]

    # I_need = 0.50 * risk_level + 0.30 * risk_acceleration + 0.20 * fragility
    need_available = sum(w for v, w in [
        (rl_v is not None, 0.50), (ra_v is not None, 0.30), (fr_v is not None, 0.20)
    ])
    need_base = need_available if need_available > 0.0 else 1.0
    rl_eff = 0.50 / need_base; ra_eff = 0.30 / need_base; fr_eff = 0.20 / need_base
    rl_contrib = (rl_v * rl_eff) if rl_v is not None else None
    ra_contrib = (ra_v * ra_eff) if ra_v is not None else None
    fr_contrib = (fr_v * fr_eff) if fr_v is not None else None

    audit["I_need"] = [
        {
            "name": "risk_level",
            "raw_indicator": float(insurance_last.get("risk_level", np.nan)) if pd.notna(insurance_last.get("risk_level", np.nan)) else None,
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
            "raw_indicator": float(insurance_last.get("risk_acceleration", np.nan)) if pd.notna(insurance_last.get("risk_acceleration", np.nan)) else None,
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
            "raw_indicator": float(insurance_last.get("fragility", np.nan)) if pd.notna(insurance_last.get("fragility", np.nan)) else None,
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

    # I_affordability = affordability (single component, weight 1.0)
    audit["I_affordability"] = [
        {
            "name": "affordability",
            "raw_indicator": afford_v,
            "data_date": latest_date,
            "source": "premium_proxy",
            "transformation": "(100 - R_proxy).clip(0, 100)",
            "normalized_value": afford_v,
            "nominal_weight": 1.0,
            "effective_weight": 1.0,
            "contribution": afford_v if afford_v is not None else None,
            "coverage": 1.0 if afford_v is not None else 0.0,
        },
    ]

    if include_audit:
        return result, components, audit
    return result, components


@dataclass
class MachineMemory:
    event_active: bool = False
    event_id: int = 0
    p_trigger_streak: int = 0
    p_exit_streak: int = 0
    event_low: float = np.inf
    event_low_position: int = -1
    current_event_state: str | None = None
    downgrade_candidate: str | None = None
    downgrade_streak: int = 0
    deployed_fraction: float = 0.0


EVENT_STATE_RANK = {
    "PANIC_ACCELERATING": 0,
    "PANIC_STABILIZING_1": 1,
    "PANIC_STABILIZING_2": 2,
    "RECOVERY_EARLY": 3,
    "RECOVERY_RETESTED": 4,
}


def _recovery_gate_passes(
    row: pd.Series,
    gate: dict[str, Any],
    *,
    days_from_low: int,
) -> bool:
    if not gate:
        return True
    checks = [
        (
            "breadth_recovery",
            "breadth_recovery_min",
            lambda value, threshold: value >= threshold,
        ),
        (
            "liquidity_relief",
            "liquidity_relief_min",
            lambda value, threshold: value >= threshold,
        ),
        (
            "tail_stress",
            "tail_stress_max",
            lambda value, threshold: value <= threshold,
        ),
    ]
    for field, threshold_name, comparator in checks:
        if threshold_name not in gate:
            continue
        value = row.get(field, np.nan)
        if pd.isna(value) or not comparator(
            float(value), float(gate[threshold_name])
        ):
            return False
    minimum_days = int(gate.get("minimum_days_from_low", 0))
    return days_from_low >= minimum_days


def run_state_machine(
    scores: pd.DataFrame,
    config: dict[str, Any],
    threshold_overrides: dict[str, float] | None = None,
) -> pd.DataFrame:
    thresholds = dict(config["thresholds"])
    if threshold_overrides:
        thresholds.update(threshold_overrides)
    deployment = config["deployment"]
    coverage_threshold = float(config["scores"]["coverage_threshold"])
    recovery_gate = (
        config.get("enriched_scores", {})
        .get("recovery_gate", {})
    )
    recovery_gate_enabled = bool(recovery_gate.get("enabled", False))
    memory = MachineMemory()
    rows: list[dict[str, Any]] = []

    for position, (date, row) in enumerate(scores.iterrows()):
        p = float(row["P"]) if pd.notna(row["P"]) else np.nan
        s = float(row["S"]) if pd.notna(row["S"]) else np.nan
        g = float(row["G"]) if pd.notna(row["G"]) else np.nan
        e = float(row["E"]) if pd.notna(row["E"]) else np.nan
        insurance_score = (
            float(row["I"]) if pd.notna(row.get("I", np.nan)) else np.nan
        )
        insurance_need = (
            float(row["I_need"])
            if pd.notna(row.get("I_need", np.nan))
            else np.nan
        )
        insurance_affordability = (
            float(row["I_affordability"])
            if pd.notna(row.get("I_affordability", np.nan))
            else np.nan
        )
        price_score = (
            float(row["P_price"]) if pd.notna(row["P_price"]) else np.nan
        )
        close = float(row["signal_close"])
        p_valid = (
            pd.notna(p)
            and float(row["P_coverage"]) >= coverage_threshold
            and pd.notna(price_score)
        )
        event_exited = False
        event_started = False

        if p_valid:
            if p >= float(thresholds["panic_trigger"]):
                memory.p_trigger_streak += 1
            else:
                memory.p_trigger_streak = 0
            trigger = (
                (
                    memory.p_trigger_streak >= 2
                    or p >= float(thresholds["panic_single_day_trigger"])
                )
                and price_score
                >= float(thresholds["panic_price_module_min"])
            )
            if trigger and not memory.event_active:
                memory.event_active = True
                memory.event_id += 1
                memory.p_exit_streak = 0
                memory.event_low = close
                memory.event_low_position = position
                memory.current_event_state = None
                memory.deployed_fraction = 0.0
                event_started = True

        new_event_low = False
        if memory.event_active and pd.notna(close):
            if close < memory.event_low:
                memory.event_low = close
                memory.event_low_position = position
                new_event_low = True
            if p_valid and p < float(thresholds["panic_exit"]):
                memory.p_exit_streak += 1
            elif p_valid:
                memory.p_exit_streak = 0
            if memory.p_exit_streak >= int(thresholds["panic_exit_days"]):
                memory.event_active = False
                memory.current_event_state = None
                memory.deployed_fraction = 0.0
                memory.p_exit_streak = 0
                event_exited = True

        s_valid = (
            pd.notna(s)
            and float(row["S_coverage"]) >= coverage_threshold
        )
        core_valid = p_valid and (not memory.event_active or s_valid)
        state = "DATA_GUARD"

        if core_valid and memory.event_active:
            days_from_low = position - memory.event_low_position
            early_gate_passed = (
                _recovery_gate_passes(
                    row,
                    recovery_gate.get("early", {}),
                    days_from_low=days_from_low,
                )
                if recovery_gate_enabled
                else True
            )
            retested_gate_passed = (
                _recovery_gate_passes(
                    row,
                    recovery_gate.get("retested", {}),
                    days_from_low=days_from_low,
                )
                if recovery_gate_enabled
                else True
            )
            retested = (
                s >= float(thresholds["recovery"])
                and close > float(row["ma20"])
                and days_from_low
                >= int(thresholds["retest_days_without_new_low"])
                and retested_gate_passed
            )
            if retested:
                candidate = "RECOVERY_RETESTED"
            elif (
                s >= float(thresholds["recovery"])
                and early_gate_passed
            ):
                candidate = "RECOVERY_EARLY"
            elif s >= float(thresholds["stabilization_2"]):
                candidate = "PANIC_STABILIZING_2"
            elif s >= float(thresholds["stabilization_1"]):
                candidate = "PANIC_STABILIZING_1"
            else:
                candidate = "PANIC_ACCELERATING"

            previous = memory.current_event_state
            if previous is None or new_event_low:
                state = candidate
                memory.current_event_state = candidate
                memory.downgrade_candidate = None
                memory.downgrade_streak = 0
            elif EVENT_STATE_RANK[candidate] >= EVENT_STATE_RANK[previous]:
                state = candidate
                memory.current_event_state = candidate
                memory.downgrade_candidate = None
                memory.downgrade_streak = 0
            else:
                if memory.downgrade_candidate == candidate:
                    memory.downgrade_streak += 1
                else:
                    memory.downgrade_candidate = candidate
                    memory.downgrade_streak = 1
                if memory.downgrade_streak >= int(
                    thresholds["downgrade_confirmation_days"]
                ):
                    state = candidate
                    memory.current_event_state = candidate
                    memory.downgrade_candidate = None
                    memory.downgrade_streak = 0
                else:
                    state = previous

            allowed = float(deployment.get(state, 0.0))
            if (
                state == "PANIC_ACCELERATING"
                and p >= float(deployment["extreme_trial_panic_score"])
            ):
                allowed = max(
                    allowed,
                    float(deployment["extreme_trial_event_fraction"]),
                )
            memory.deployed_fraction = max(
                memory.deployed_fraction, allowed
            )
        elif core_valid:
            memory.deployed_fraction = 0.0
            if (
                p >= float(thresholds["panic_exit"])
                and p < float(thresholds["panic_trigger"])
            ):
                state = "RISK_WARNING"
            elif (
                pd.notna(g)
                and pd.notna(e)
                and g >= float(thresholds["greed"])
                and e >= float(thresholds["exhaustion"])
            ):
                state = "GREED_EXHAUSTING"
            elif pd.notna(g) and g >= float(thresholds["greed"]):
                state = "GREED_TREND"
            elif bool(row["range_condition"]):
                state = "RANGE_CARRY"
            else:
                state = "NORMAL_PARTICIPATION"

        rows.append(
            {
                "date": date,
                "state": state,
                "event_active": bool(memory.event_active),
                "event_id": (
                    int(memory.event_id)
                    if memory.event_active or event_exited
                    else 0
                ),
                "event_started": event_started,
                "event_exited": event_exited,
                "event_low": (
                    memory.event_low if memory.event_active else np.nan
                ),
                "deployment_fraction": float(memory.deployed_fraction),
                "P": p,
                "S": s,
                "G": g,
                "E": e,
                "R_proxy": (
                    float(row["R_proxy"])
                    if pd.notna(row["R_proxy"])
                    else np.nan
                ),
                "R_put": (
                    float(row.get("R_put", row["R_proxy"]))
                    if pd.notna(row.get("R_put", row["R_proxy"]))
                    else np.nan
                ),
                "R_call": (
                    float(row.get("R_call", row["R_proxy"]))
                    if pd.notna(row.get("R_call", row["R_proxy"]))
                    else np.nan
                ),
                "breadth_stress": row.get("breadth_stress", np.nan),
                "breadth_recovery": row.get(
                    "breadth_recovery", np.nan
                ),
                "liquidity_stress": row.get(
                    "liquidity_stress", np.nan
                ),
                "liquidity_relief": row.get(
                    "liquidity_relief", np.nan
                ),
                "tail_stress": row.get("tail_stress", np.nan),
                "I": insurance_score,
                "I_need": insurance_need,
                "I_affordability": insurance_affordability,
            }
        )

    result = pd.DataFrame(rows).set_index("date")
    result.index = pd.to_datetime(result.index)
    return result
