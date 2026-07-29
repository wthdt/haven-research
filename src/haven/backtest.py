from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def _recovery_details(equity: pd.Series) -> dict[str, Any]:
    drawdown = _drawdown(equity)
    trough_date = drawdown.idxmin()
    peak_date = equity.loc[:trough_date].idxmax()
    peak_value = float(equity.loc[peak_date])
    future = equity.loc[trough_date:]
    recovered = future[future >= peak_value]
    if len(recovered):
        recovery_date = recovered.index[0]
        recovery_days = int(equity.loc[peak_date:recovery_date].shape[0] - 1)
        recovery_date_text: str | None = str(recovery_date.date())
    else:
        recovery_days = int(equity.loc[peak_date:].shape[0] - 1)
        recovery_date_text = None
    return {
        "drawdown_peak_date": str(peak_date.date()),
        "drawdown_trough_date": str(trough_date.date()),
        "recovery_date": recovery_date_text,
        "recovery_trading_days": recovery_days,
        "recovered": bool(len(recovered)),
    }


def _rolling_compound(returns: pd.Series, days: int) -> pd.Series:
    return (1.0 + returns).rolling(days).apply(np.prod, raw=True) - 1.0


def performance_metrics(
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    returns = frame["portfolio_return"].dropna()
    cash_returns = frame.loc[returns.index, "cash_return"].fillna(0.0)
    equity = frame.loc[returns.index, "equity"]
    annualization = int(config["backtest"]["annualization_days"])
    years = max(
        (returns.index[-1] - returns.index[0]).days / 365.25,
        len(returns) / annualization,
    )
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)
    annual_volatility = float(returns.std(ddof=1) * np.sqrt(annualization))
    excess = returns - cash_returns
    excess_std = float(excess.std(ddof=1))
    sharpe = (
        float(excess.mean() / excess_std * np.sqrt(annualization))
        if excess_std > 0.0
        else np.nan
    )
    downside = excess.clip(upper=0.0)
    downside_deviation = float(
        np.sqrt((downside.pow(2)).mean()) * np.sqrt(annualization)
    )
    sortino = (
        float(excess.mean() * annualization / downside_deviation)
        if downside_deviation > 0.0
        else np.nan
    )
    drawdown = _drawdown(equity)
    alpha = float(config["backtest"]["cvar_alpha"])
    quantile = float(returns.quantile(alpha))
    cvar = float(returns[returns <= quantile].mean())
    weight_column = (
        "effective_asset_weight"
        if "effective_asset_weight" in frame.columns
        else "executed_asset_weight"
    )
    average_asset_weight = float(frame[weight_column].mean())
    result: dict[str, Any] = {
        "start": str(returns.index.min().date()),
        "end": str(returns.index.max().date()),
        "trading_days": int(len(returns)),
        "total_return": total_return,
        "cagr": cagr,
        "annual_volatility": annual_volatility,
        "sharpe_excess_bil": sharpe,
        "sortino_excess_bil": sortino,
        "max_drawdown": float(drawdown.min()),
        "daily_var_95": quantile,
        "daily_cvar_95": cvar,
        "worst_1d": float(returns.min()),
        "worst_5d": float(_rolling_compound(returns, 5).min()),
        "worst_20d": float(_rolling_compound(returns, 20).min()),
        "worst_63d": float(_rolling_compound(returns, 63).min()),
        "average_asset_weight": average_asset_weight,
        "average_cash_weight": float(1.0 - average_asset_weight),
        "annual_turnover": float(
            frame["turnover"].sum() / max(years, 1e-9)
        ),
        "transaction_cost_total": float(frame["transaction_cost"].sum()),
    }
    result.update(_recovery_details(equity))
    return result


def _target_weight(
    states: pd.DataFrame,
    capital_split: dict[str, float],
    config: dict[str, Any],
) -> pd.Series:
    baseline = float(capital_split["baseline"])
    reserve = float(capital_split["panic_reserve"])
    deployed = states["deployment_fraction"].fillna(0.0).clip(0.0, 1.0)
    extra = reserve * deployed
    accelerating = states["state"].eq("PANIC_ACCELERATING") & deployed.gt(0.0)
    extra.loc[accelerating] = np.minimum(
        extra.loc[accelerating],
        float(config["deployment"]["extreme_trial_nav_cap"]),
    )
    target = baseline + extra
    return target.clip(lower=0.0, upper=baseline + reserve)


def run_strategy_backtest(
    data: pd.DataFrame,
    states: pd.DataFrame,
    asset_symbol: str,
    capital_split: dict[str, float],
    config: dict[str, Any],
) -> pd.DataFrame:
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]
    symbol = asset_symbol.lower()
    target_full = _target_weight(states, capital_split, config)
    executed_full = target_full.shift(1).fillna(float(capital_split["baseline"]))

    frame = pd.DataFrame(index=data.index)
    frame["state"] = states["state"]
    frame["event_id"] = states["event_id"]
    frame["event_active"] = states["event_active"]
    frame["deployment_fraction"] = states["deployment_fraction"]
    frame["P"] = states["P"]
    frame["S"] = states["S"]
    frame["G"] = states["G"]
    frame["E"] = states["E"]
    frame["R_proxy"] = states["R_proxy"]
    for column in ["R_put", "R_call"]:
        if column in states:
            frame[column] = states[column]
    for column in ["I", "I_need", "I_affordability"]:
        if column in states:
            frame[column] = states[column]
    frame["target_asset_weight"] = target_full
    frame["executed_asset_weight"] = executed_full
    frame["asset_return"] = data[f"{symbol}_total_return"].fillna(0.0)
    frame["cash_return"] = data["cash_total_return"].fillna(0.0)
    frame = frame.loc[start:end].copy()

    frame["turnover"] = (
        frame["executed_asset_weight"].diff().abs().fillna(0.0)
    )
    cost_rate = float(config["backtest"]["transaction_cost_bps"]) / 10000.0
    frame["transaction_cost"] = frame["turnover"] * cost_rate
    frame["gross_return"] = (
        frame["executed_asset_weight"] * frame["asset_return"]
        + (1.0 - frame["executed_asset_weight"]) * frame["cash_return"]
    )
    frame["portfolio_return"] = (
        frame["gross_return"] - frame["transaction_cost"]
    )
    initial_nav = float(config["strategy"]["initial_nav"])
    frame["equity"] = initial_nav * (1.0 + frame["portfolio_return"]).cumprod()
    frame["drawdown"] = _drawdown(frame["equity"])
    return frame


def run_fixed_weight_backtest(
    data: pd.DataFrame,
    asset_symbol: str,
    asset_weight: float,
    config: dict[str, Any],
) -> pd.DataFrame:
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]
    symbol = asset_symbol.lower()
    frame = pd.DataFrame(index=data.loc[start:end].index)
    frame["executed_asset_weight"] = float(asset_weight)
    frame["target_asset_weight"] = float(asset_weight)
    frame["asset_return"] = data.loc[start:end, f"{symbol}_total_return"].fillna(
        0.0
    )
    frame["cash_return"] = data.loc[start:end, "cash_total_return"].fillna(0.0)
    frame["turnover"] = 0.0
    frame["transaction_cost"] = 0.0
    frame["gross_return"] = (
        asset_weight * frame["asset_return"]
        + (1.0 - asset_weight) * frame["cash_return"]
    )
    frame["portfolio_return"] = frame["gross_return"]
    initial_nav = float(config["strategy"]["initial_nav"])
    frame["equity"] = initial_nav * (1.0 + frame["portfolio_return"]).cumprod()
    frame["drawdown"] = _drawdown(frame["equity"])
    return frame


def stress_period_metrics(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    periods = {
        "2018_Q4": ("2018-10-01", "2018-12-31"),
        "COVID_crash_recovery": ("2020-02-19", "2020-04-30"),
        "2022_bear": ("2022-01-03", "2022-12-30"),
    }
    rows: list[dict[str, Any]] = []
    for name, (start, end) in periods.items():
        section = frame.loc[start:end]
        if section.empty:
            continue
        returns = section["portfolio_return"]
        equity = (1.0 + returns).cumprod()
        rows.append(
            {
                "period": name,
                "start": str(section.index.min().date()),
                "end": str(section.index.max().date()),
                "return": float(equity.iloc[-1] - 1.0),
                "max_drawdown": float(_drawdown(equity).min()),
                "worst_day": float(returns.min()),
            }
        )
    return pd.DataFrame(rows)


def build_event_log(
    states: pd.DataFrame,
    data: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]
    joined = states.loc[start:end].copy()
    joined["signal_close"] = data.loc[start:end, "ndx_close"]
    events: list[dict[str, Any]] = []
    for event_id, group in joined[joined["event_id"] > 0].groupby("event_id"):
        active = group[group["event_active"]]
        if active.empty:
            continue
        start_date = active.index.min()
        exit_rows = group[group["event_exited"]]
        end_date = (
            exit_rows.index.max()
            if not exit_rows.empty
            else active.index.max()
        )
        window = joined.loc[start_date:end_date]
        low_date = window["signal_close"].idxmin()
        start_close = float(window["signal_close"].iloc[0])
        end_close = float(window["signal_close"].iloc[-1])
        events.append(
            {
                "event_id": int(event_id),
                "start": str(start_date.date()),
                "end": str(end_date.date()),
                "completed": bool(not exit_rows.empty),
                "trading_days": int(len(window)),
                "low_date": str(low_date.date()),
                "max_P": float(window["P"].max()),
                "max_S": float(window["S"].max()),
                "max_deployment_fraction": float(
                    window["deployment_fraction"].max()
                ),
                "ndx_return_during_event": end_close / start_close - 1.0,
                "ndx_max_drawdown_from_start": float(
                    (window["signal_close"] / start_close - 1.0).min()
                ),
            }
        )
    return pd.DataFrame(events)


def account_risk_translation(
    capital_splits: dict[str, dict[str, float]],
    config: dict[str, Any],
) -> pd.DataFrame:
    risk = config["risk_research_defaults"]
    rows: list[dict[str, Any]] = []
    for split_name, split in capital_splits.items():
        baseline = float(split["baseline"])
        max_weight = baseline + float(split["panic_reserve"])
        for asset, stress_drop, limit in [
            (
                "QQQ",
                float(risk["qqq_stress_drop"]),
                float(risk["normal_risk_factor_stress_loss_limit"]),
            ),
            (
                "TQQQ",
                float(risk["tqqq_stress_drop"]),
                float(risk["leveraged_etf_stress_loss_limit"]),
            ),
        ]:
            baseline_sleeve_limit = min(
                1.0, limit / max(baseline * stress_drop, 1e-12)
            )
            max_sleeve_limit = min(
                1.0, limit / max(max_weight * stress_drop, 1e-12)
            )
            rows.append(
                {
                    "capital_split": split_name,
                    "asset": asset,
                    "stress_drop_assumption": stress_drop,
                    "risk_loss_limit": limit,
                    "baseline_asset_weight_in_sleeve": baseline,
                    "max_asset_weight_in_sleeve": max_weight,
                    "max_account_share_at_baseline": baseline_sleeve_limit,
                    "max_account_share_at_full_deployment": max_sleeve_limit,
                }
            )
    return pd.DataFrame(rows)
