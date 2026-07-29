from __future__ import annotations

import calendar
import copy
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .backtest import run_strategy_backtest


@dataclass
class OptionQuote:
    mid: float
    bid: float
    ask: float
    delta: float
    iv: float
    dte: int


@dataclass
class OptionLeg:
    option_type: str
    side: int
    strike: float
    expiry: pd.Timestamp
    contracts: int
    entry_exec: float
    entry_mid: float
    last_mid: float


@dataclass
class OptionGroup:
    group_id: int
    strategy_type: str
    entry_date: pd.Timestamp
    entry_spot: float
    initial_contracts: int
    current_contracts: int
    entry_cash_flow: float
    entry_unit_cash_flow: float
    entry_commission: float
    legs: list[OptionLeg] = field(default_factory=list)
    monetization_stage: int = 0
    total_pnl: float = 0.0
    collateral: float = 0.0
    close_reason: str | None = None


@dataclass
class AssignedLot:
    lot_id: int
    assignment_date: pd.Timestamp
    shares: int
    basis: float
    source_group_id: int
    holding_days: int = 0


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def black_scholes_price_delta(
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: str,
) -> tuple[float, float]:
    """European proxy used only for the synthetic option scenario."""

    if time_years <= 0.0 or volatility <= 0.0:
        if option_type == "call":
            return max(spot - strike, 0.0), 1.0 if spot > strike else 0.0
        return max(strike - spot, 0.0), -1.0 if spot < strike else 0.0
    sqrt_t = math.sqrt(time_years)
    d1 = (
        math.log(max(spot, 1e-12) / max(strike, 1e-12))
        + (rate - dividend_yield + 0.5 * volatility**2) * time_years
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    discount_r = math.exp(-rate * time_years)
    discount_q = math.exp(-dividend_yield * time_years)
    if option_type == "call":
        price = (
            spot * discount_q * _norm_cdf(d1)
            - strike * discount_r * _norm_cdf(d2)
        )
        delta = discount_q * _norm_cdf(d1)
    elif option_type == "put":
        price = (
            strike * discount_r * _norm_cdf(-d2)
            - spot * discount_q * _norm_cdf(-d1)
        )
        delta = discount_q * (_norm_cdf(d1) - 1.0)
    else:
        raise ValueError(f"未知期权类型：{option_type}")
    return max(float(price), 0.0), float(delta)


def build_option_proxy_inputs(
    data: pd.DataFrame,
    asset_symbol: str,
    config: dict[str, Any],
    option_overrides: dict[str, Any] | None = None,
) -> pd.DataFrame:
    symbol = asset_symbol.lower()
    option_config = copy.deepcopy(config["options_proxy"])
    if option_overrides:
        option_config.update(option_overrides)
    asset_returns = data[f"{symbol}_price_return"].astype(float)
    qqq_returns = data["qqq_price_return"].astype(float)
    asset_rv20 = asset_returns.rolling(20).std() * math.sqrt(252.0)
    qqq_rv20 = qqq_returns.rolling(20).std() * math.sqrt(252.0)
    if asset_symbol.upper() == "QQQ":
        leverage_ratio = pd.Series(1.0, index=data.index)
        iv_floor = float(option_config["qqq_iv_floor"])
        iv_cap = float(option_config["qqq_iv_cap"])
        spread_fraction = float(option_config["qqq_spread_fraction"])
    else:
        daily_ratio = asset_rv20 / qqq_rv20.replace(0.0, np.nan)
        leverage_ratio = daily_ratio.rolling(
            63, min_periods=20
        ).median().clip(
            float(option_config["leverage_iv_ratio_floor"]),
            float(option_config["leverage_iv_ratio_cap"]),
        )
        leverage_ratio = leverage_ratio.fillna(
            float(option_config["leverage_iv_ratio_floor"])
        )
        iv_floor = float(option_config["tqqq_iv_floor"])
        iv_cap = float(option_config["tqqq_iv_cap"])
        spread_fraction = float(option_config["tqqq_spread_fraction"])

    vxn = data["vxn_close"].astype(float) / 100.0
    iv_multiplier = float(option_config["iv_multiplier"])
    atm_iv = (vxn * leverage_ratio * iv_multiplier).combine(
        asset_rv20 * 0.95,
        max,
    )
    atm_iv = atm_iv.clip(iv_floor, iv_cap)
    long_run_iv = atm_iv.rolling(
        int(option_config["term_mean_reversion_days"]),
        min_periods=63,
    ).median().combine_first(atm_iv)

    spot = data[f"{symbol}_close"].astype(float)
    dividend = data[f"{symbol}_dividend"].fillna(0.0).astype(float)
    trailing_dividend = dividend.rolling(252, min_periods=20).sum()
    dividend_yield = (trailing_dividend / spot).clip(0.0, 0.15).fillna(0.0)

    result = pd.DataFrame(index=data.index)
    result["spot"] = spot
    result["asset_total_return"] = data[f"{symbol}_total_return"].fillna(0.0)
    result["asset_price_return"] = asset_returns.fillna(0.0)
    result["dividend"] = dividend
    result["rate"] = (
        data["cash_yield_pct"].fillna(0.0).astype(float) / 100.0
    )
    result["dividend_yield"] = dividend_yield
    result["asset_rv20"] = asset_rv20
    result["qqq_rv20"] = qqq_rv20
    result["leverage_iv_ratio"] = leverage_ratio
    result["atm_iv"] = atm_iv
    result["long_run_iv"] = long_run_iv
    result["spread_fraction"] = spread_fraction
    return result


def _surface_iv(
    market: pd.Series,
    strike: float,
    dte: int,
    option_type: str,
    option_config: dict[str, Any],
    asset_symbol: str,
) -> float:
    spot = float(market["spot"])
    atm = float(market["atm_iv"])
    long_run = float(market["long_run_iv"])
    if dte <= 30:
        term_iv = atm
    else:
        term_iv = math.sqrt(
            (atm**2 * 30.0 + long_run**2 * float(dte - 30))
            / float(dte)
        )
    if option_type == "put":
        otm = max(0.0, (spot - strike) / max(spot, 1e-12))
        term_iv *= 1.0 + float(
            option_config["put_skew_per_10pct_otm"]
        ) * otm / 0.10
    else:
        otm = max(0.0, (strike - spot) / max(spot, 1e-12))
        term_iv *= 1.0 + float(
            option_config["call_skew_per_10pct_otm"]
        ) * otm / 0.10
    if asset_symbol.upper() == "QQQ":
        floor = float(option_config["qqq_iv_floor"])
        cap = float(option_config["qqq_iv_cap"])
    else:
        floor = float(option_config["tqqq_iv_floor"])
        cap = float(option_config["tqqq_iv_cap"])
    return float(np.clip(term_iv, floor, cap))


def option_quote(
    date: pd.Timestamp,
    expiry: pd.Timestamp,
    strike: float,
    option_type: str,
    market: pd.Series,
    option_config: dict[str, Any],
    asset_symbol: str,
) -> OptionQuote:
    dte = max(int((expiry - date).days), 0)
    spot = float(market["spot"])
    if dte == 0:
        intrinsic = (
            max(spot - strike, 0.0)
            if option_type == "call"
            else max(strike - spot, 0.0)
        )
        delta = (
            (1.0 if spot > strike else 0.0)
            if option_type == "call"
            else (-1.0 if spot < strike else 0.0)
        )
        return OptionQuote(intrinsic, intrinsic, intrinsic, delta, 0.0, 0)
    iv = _surface_iv(
        market, strike, dte, option_type, option_config, asset_symbol
    )
    mid, delta = black_scholes_price_delta(
        spot=spot,
        strike=strike,
        time_years=float(dte) / 365.0,
        rate=float(market["rate"]),
        dividend_yield=float(market["dividend_yield"]),
        volatility=iv,
        option_type=option_type,
    )
    full_spread = max(
        float(option_config["minimum_full_spread_per_share"]),
        mid
        * float(market["spread_fraction"])
        * float(option_config["spread_multiplier"]),
    )
    bid = max(0.0, mid - full_spread / 2.0)
    ask = mid + full_spread / 2.0
    return OptionQuote(
        mid=float(mid),
        bid=float(bid),
        ask=float(ask),
        delta=float(delta),
        iv=float(iv),
        dte=dte,
    )


def _third_friday(year: int, month: int) -> pd.Timestamp:
    cal = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in cal if week[calendar.FRIDAY]]
    return pd.Timestamp(year=year, month=month, day=fridays[2])


def choose_expiry(
    date: pd.Timestamp,
    trading_index: pd.DatetimeIndex,
    target_dte: int,
    minimum_dte: int,
    maximum_dte: int,
) -> pd.Timestamp | None:
    candidates: list[pd.Timestamp] = []
    for offset in range(0, 7):
        month_number = date.month - 1 + offset
        year = date.year + month_number // 12
        month = month_number % 12 + 1
        calendar_expiry = _third_friday(year, month)
        eligible = trading_index[
            (trading_index > date) & (trading_index <= calendar_expiry)
        ]
        if len(eligible) == 0:
            continue
        expiry = eligible[-1]
        dte = int((expiry - date).days)
        if minimum_dte <= dte <= maximum_dte:
            candidates.append(expiry)
    if not candidates:
        return None
    return min(candidates, key=lambda value: abs((value - date).days - target_dte))


def _strike_step(spot: float) -> float:
    return 0.5 if spot < 25.0 else 1.0


def choose_strike_for_delta(
    date: pd.Timestamp,
    expiry: pd.Timestamp,
    target_abs_delta: float,
    option_type: str,
    market: pd.Series,
    option_config: dict[str, Any],
    asset_symbol: str,
) -> tuple[float, OptionQuote] | None:
    spot = float(market["spot"])
    step = _strike_step(spot)
    if option_type == "call":
        start = math.ceil(spot / step) * step
        end = spot * 1.45
    else:
        start = max(step, math.floor(spot * 0.55 / step) * step)
        end = math.floor(spot / step) * step
    strikes = np.arange(start, end + step * 0.5, step)
    if option_type == "call":
        strikes = strikes[strikes >= spot]
    else:
        strikes = strikes[strikes <= spot]
    best: tuple[float, OptionQuote] | None = None
    best_error = float("inf")
    for strike in strikes:
        quote = option_quote(
            date,
            expiry,
            float(strike),
            option_type,
            market,
            option_config,
            asset_symbol,
        )
        error = abs(abs(quote.delta) - target_abs_delta)
        if error < best_error:
            best = (float(strike), quote)
            best_error = error
    return best


def _call_terms(
    decision: pd.Series,
    option_config: dict[str, Any],
) -> tuple[float, float, float] | None:
    call_config = option_config["covered_call"]
    state = str(decision["state"])
    g = float(decision["G"])
    e = float(decision["E"])
    r = float(decision.get("R_call", decision["R_proxy"]))
    if state == "RANGE_CARRY":
        if (
            g >= float(call_config["range_g_min"])
            and e >= float(call_config["range_e_min"])
            and r >= float(call_config["range_r_min"])
        ):
            return (
                float(call_config["range_max_coverage"]),
                float(call_config["range_target_delta"]),
                float(call_config["range_minimum_upside_buffer"]),
            )
    elif state == "GREED_TREND":
        if (
            g >= float(call_config["greed_trend_g_min"])
            and r >= float(call_config["greed_trend_r_min"])
        ):
            return (
                float(call_config["greed_trend_max_coverage"]),
                float(call_config["greed_trend_target_delta"]),
                float(call_config["greed_trend_minimum_upside_buffer"]),
            )
    elif state == "GREED_EXHAUSTING":
        if r < float(call_config["exhausting_r_min"]):
            return None
        high = (
            g >= float(call_config["exhausting_high_g_min"])
            and e >= float(call_config["exhausting_high_e_min"])
            and r >= float(call_config["exhausting_high_r_min"])
        )
        coverage = float(
            call_config[
                "greed_exhausting_high_coverage"
                if high
                else "greed_exhausting_base_coverage"
            ]
        )
        return (
            coverage,
            float(call_config["greed_exhausting_target_delta"]),
            float(call_config["greed_exhausting_minimum_upside_buffer"]),
        )
    return None


def _csp_terms(
    decision: pd.Series,
    option_config: dict[str, Any],
) -> tuple[float, float, float] | None:
    put_config = option_config["cash_secured_put"]
    state = str(decision["state"])
    r = float(decision.get("R_put", decision["R_proxy"]))
    safety_gates = {
        "P": ("entry_p_max", lambda value, limit: value <= limit),
        "E": ("entry_e_max", lambda value, limit: value <= limit),
        "breadth_stress": (
            "entry_breadth_stress_max",
            lambda value, limit: value <= limit,
        ),
        "liquidity_stress": (
            "entry_liquidity_stress_max",
            lambda value, limit: value <= limit,
        ),
    }
    for field, (config_name, comparator) in safety_gates.items():
        if config_name not in put_config:
            continue
        value = decision.get(field, np.nan)
        if pd.isna(value) or not comparator(
            float(value), float(put_config[config_name])
        ):
            return None
    if state == "RANGE_CARRY" and r >= float(put_config["range_r_min"]):
        return (
            float(put_config["range_bucket_fraction"]),
            float(put_config["range_target_delta"]),
            float(put_config["range_minimum_net_discount"]),
        )
    if (
        state == "PANIC_STABILIZING_1"
        and r >= float(put_config["panic_r_min"])
    ):
        return (
            float(put_config["stabilizing_1_bucket_fraction"]),
            float(put_config["panic_target_delta"]),
            float(put_config["stabilizing_1_minimum_net_discount"]),
        )
    if (
        state == "PANIC_STABILIZING_2"
        and r >= float(put_config["panic_r_min"])
    ):
        return (
            float(put_config["stabilizing_2_bucket_fraction"]),
            float(put_config["panic_target_delta"]),
            float(put_config["stabilizing_2_minimum_net_discount"]),
        )
    return None


def _merge_option_overrides(
    option_config: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    result = copy.deepcopy(option_config)
    if not overrides:
        return result
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    return result


def run_options_proxy_backtest(
    data: pd.DataFrame,
    states: pd.DataFrame,
    asset_symbol: str,
    capital_split: dict[str, float],
    config: dict[str, Any],
    modules: dict[str, bool] | None = None,
    option_overrides: dict[str, Any] | None = None,
    strategy_name: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run a marked-to-model option overlay with one-day-delayed signals."""

    enabled = {
        "covered_call": True,
        "insurance": True,
        "cash_secured_put": True,
    }
    if modules:
        enabled.update(modules)
    option_config = _merge_option_overrides(
        config["options_proxy"], option_overrides
    )
    base = run_strategy_backtest(
        data, states, asset_symbol, capital_split, config
    )
    proxy_inputs_all = build_option_proxy_inputs(
        data, asset_symbol, config, option_overrides
    ).loc[base.index]
    # The joined macro calendar contains one historical Good Friday row with
    # no Nasdaq print. It is not a tradable/markable option date and must not
    # be converted into a synthetic zero-return trading session.
    valid_option_dates = proxy_inputs_all["spot"].notna()
    base = base.loc[valid_option_dates].copy()
    proxy_inputs = proxy_inputs_all.loc[valid_option_dates].copy()
    index = base.index
    # State scores are available only after the signal close. Reindex first,
    # then delay one trading day so warm-up rows cannot shift evaluation dates.
    decision_states = states.reindex(index).shift(1)
    multiplier = int(option_config["contract_multiplier"])
    commission = float(option_config["commission_per_contract_per_leg"])
    minimum_price = float(option_config["minimum_option_price"])
    min_free_cash = float(
        config["risk_research_defaults"]["minimum_free_cash"]
    )
    transaction_cost_rate = (
        float(config["backtest"]["transaction_cost_bps"]) / 10000.0
    )

    groups: dict[str, OptionGroup | None] = {
        "covered_call": None,
        "insurance": None,
        "cash_secured_put": None,
    }
    assigned_lots: list[AssignedLot] = []
    trade_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    annual_insurance_debit: dict[int, float] = {}
    group_counter = 0
    lot_counter = 0
    call_lockout_until = -1
    insurance_alert_until = -1
    cooldown_until = {
        "covered_call": -1,
        "insurance": -1,
        "cash_secured_put": -1,
    }
    nav = float(config["strategy"]["initial_nav"])

    def record_trade(
        date: pd.Timestamp,
        group: OptionGroup,
        action: str,
        leg: OptionLeg | None,
        contracts: int,
        quote: OptionQuote | None,
        execution_price: float | None,
        cash_flow: float,
        pnl: float,
        execution_drag: float,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "date": date,
            "strategy_name": strategy_name or asset_symbol,
            "asset": asset_symbol,
            "group_id": group.group_id,
            "strategy_type": group.strategy_type,
            "action": action,
            "option_type": leg.option_type if leg else None,
            "side": leg.side if leg else None,
            "strike": leg.strike if leg else np.nan,
            "expiry": leg.expiry if leg else pd.NaT,
            "contracts": contracts,
            "spot": float(proxy_inputs.loc[date, "spot"]),
            "mid": quote.mid if quote else np.nan,
            "bid": quote.bid if quote else np.nan,
            "ask": quote.ask if quote else np.nan,
            "execution_price": (
                execution_price if execution_price is not None else np.nan
            ),
            "iv": quote.iv if quote else np.nan,
            "delta": quote.delta if quote else np.nan,
            "cash_flow": cash_flow,
            "pnl": pnl,
            "execution_drag": execution_drag,
            "reason": reason,
        }
        if extra:
            row.update(extra)
        trade_rows.append(row)

    def open_group(
        date: pd.Timestamp,
        strategy_type: str,
        leg_specs: list[tuple[str, int, float, pd.Timestamp, OptionQuote]],
        contracts: int,
        collateral: float,
        reason: str,
    ) -> tuple[OptionGroup, float, float, dict[str, float]]:
        nonlocal group_counter
        group_counter += 1
        legs: list[OptionLeg] = []
        opening_pnl = 0.0
        cash_flow = 0.0
        execution_drag = 0.0
        premiums = {
            "call_premium_collected": 0.0,
            "csp_premium_collected": 0.0,
            "insurance_long_premium_paid": 0.0,
            "insurance_short_premium_collected": 0.0,
        }
        for option_type, side, strike, expiry, quote in leg_specs:
            execution_price = quote.ask if side > 0 else quote.bid
            leg_cash_flow = -side * execution_price * multiplier * contracts
            leg_slippage = (
                side * (quote.mid - execution_price) * multiplier * contracts
            )
            leg_commission = commission * contracts
            leg_pnl = leg_slippage - leg_commission
            opening_pnl += leg_pnl
            execution_drag += -leg_slippage + leg_commission
            cash_flow += leg_cash_flow
            leg = OptionLeg(
                option_type=option_type,
                side=side,
                strike=float(strike),
                expiry=expiry,
                contracts=contracts,
                entry_exec=float(execution_price),
                entry_mid=float(quote.mid),
                last_mid=float(quote.mid),
            )
            legs.append(leg)
            if strategy_type == "covered_call" and side < 0:
                premiums["call_premium_collected"] += leg_cash_flow
            elif strategy_type == "cash_secured_put" and side < 0:
                premiums["csp_premium_collected"] += leg_cash_flow
            elif strategy_type == "insurance" and side > 0:
                premiums["insurance_long_premium_paid"] += -leg_cash_flow
            elif strategy_type == "insurance" and side < 0:
                premiums["insurance_short_premium_collected"] += leg_cash_flow
        entry_commission = commission * contracts * len(legs)
        group = OptionGroup(
            group_id=group_counter,
            strategy_type=strategy_type,
            entry_date=date,
            entry_spot=float(proxy_inputs.loc[date, "spot"]),
            initial_contracts=contracts,
            current_contracts=contracts,
            entry_cash_flow=cash_flow,
            entry_unit_cash_flow=cash_flow / max(contracts, 1),
            entry_commission=entry_commission,
            legs=legs,
            total_pnl=opening_pnl,
            collateral=collateral,
        )
        for leg, (_, _, _, _, quote) in zip(legs, leg_specs):
            execution_price = leg.entry_exec
            leg_cash_flow = -leg.side * execution_price * multiplier * contracts
            leg_slippage = (
                leg.side
                * (quote.mid - execution_price)
                * multiplier
                * contracts
            )
            leg_pnl = leg_slippage - commission * contracts
            record_trade(
                date,
                group,
                "OPEN",
                leg,
                contracts,
                quote,
                execution_price,
                leg_cash_flow,
                leg_pnl,
                -leg_slippage + commission * contracts,
                reason,
            )
        return group, opening_pnl, execution_drag, premiums

    def mark_group(
        date: pd.Timestamp,
        group: OptionGroup,
    ) -> tuple[float, dict[int, OptionQuote]]:
        market = proxy_inputs.loc[date]
        pnl = 0.0
        quotes: dict[int, OptionQuote] = {}
        for leg_index, leg in enumerate(group.legs):
            quote = option_quote(
                date,
                leg.expiry,
                leg.strike,
                leg.option_type,
                market,
                option_config,
                asset_symbol,
            )
            leg_pnl = (
                leg.side
                * (quote.mid - leg.last_mid)
                * multiplier
                * leg.contracts
            )
            pnl += leg_pnl
            leg.last_mid = quote.mid
            quotes[leg_index] = quote
        group.total_pnl += pnl
        return pnl, quotes

    def close_group_quantity(
        date: pd.Timestamp,
        group: OptionGroup,
        quantity: int,
        reason: str,
        settle_at_expiry: bool = False,
    ) -> tuple[float, float]:
        quantity = min(quantity, group.current_contracts)
        if quantity <= 0:
            return 0.0, 0.0
        market = proxy_inputs.loc[date]
        pnl = 0.0
        execution_drag = 0.0
        for leg in group.legs:
            quote = option_quote(
                date,
                leg.expiry,
                leg.strike,
                leg.option_type,
                market,
                option_config,
                asset_symbol,
            )
            if settle_at_expiry:
                execution_price = quote.mid
                leg_commission = 0.0
            else:
                execution_price = quote.bid if leg.side > 0 else quote.ask
                leg_commission = commission * quantity
            cash_flow = (
                leg.side * execution_price * multiplier * quantity
            )
            leg_pnl = (
                leg.side
                * (execution_price - quote.mid)
                * multiplier
                * quantity
                - leg_commission
            )
            pnl += leg_pnl
            slippage_drag = (
                -leg.side
                * (execution_price - quote.mid)
                * multiplier
                * quantity
            )
            execution_drag += slippage_drag + leg_commission
            record_trade(
                date,
                group,
                "SETTLE" if settle_at_expiry else "CLOSE",
                leg,
                quantity,
                quote,
                execution_price,
                cash_flow,
                leg_pnl,
                slippage_drag + leg_commission,
                reason,
            )
            leg.contracts -= quantity
        group.current_contracts -= quantity
        group.total_pnl += pnl
        if group.current_contracts == 0:
            group.close_reason = reason
        return pnl, execution_drag

    def group_liquidation_value(
        date: pd.Timestamp,
        group: OptionGroup,
    ) -> float:
        market = proxy_inputs.loc[date]
        value = 0.0
        for leg in group.legs:
            quote = option_quote(
                date,
                leg.expiry,
                leg.strike,
                leg.option_type,
                market,
                option_config,
                asset_symbol,
            )
            execution_price = quote.bid if leg.side > 0 else quote.ask
            value += (
                leg.side
                * execution_price
                * multiplier
                * group.current_contracts
            )
        value -= commission * group.current_contracts * len(group.legs)
        return value

    for i, date in enumerate(index):
        nav_start = nav
        market = proxy_inputs.loc[date]
        spot = float(market["spot"])
        previous_spot = (
            float(proxy_inputs.iloc[i - 1]["spot"]) if i > 0 else spot
        )
        assigned_value_start = sum(
            lot.shares * previous_spot for lot in assigned_lots
        )
        assigned_excess_pnl = (
            assigned_value_start
            * (
                float(base.iloc[i]["asset_return"])
                - float(base.iloc[i]["cash_return"])
            )
            if i > 0
            else 0.0
        )
        for lot in assigned_lots:
            lot.holding_days += 1

        components = {
            "covered_call_pnl": 0.0,
            "cash_secured_put_pnl": 0.0,
            "insurance_pnl": 0.0,
            "assigned_inventory_pnl": assigned_excess_pnl,
            "execution_drag": 0.0,
            "call_premium_collected": 0.0,
            "csp_premium_collected": 0.0,
            "insurance_long_premium_paid": 0.0,
            "insurance_short_premium_collected": 0.0,
        }

        for strategy_type in list(groups):
            group = groups[strategy_type]
            if group is None:
                continue
            mark_pnl, _ = mark_group(date, group)
            components[f"{strategy_type}_pnl"] += mark_pnl
            expiry = min(leg.expiry for leg in group.legs)
            if date >= expiry:
                csp_leg = (
                    group.legs[0]
                    if strategy_type == "cash_secured_put"
                    else None
                )
                call_leg = (
                    group.legs[0]
                    if strategy_type == "covered_call"
                    else None
                )
                quantity = group.current_contracts
                settle_pnl, settle_drag = close_group_quantity(
                    date,
                    group,
                    quantity,
                    "EXPIRY",
                    settle_at_expiry=True,
                )
                components[f"{strategy_type}_pnl"] += settle_pnl
                components["execution_drag"] += settle_drag
                if (
                    csp_leg is not None
                    and spot < csp_leg.strike
                    and quantity > 0
                ):
                    lot_counter += 1
                    credit_per_share = max(
                        group.entry_unit_cash_flow / multiplier, 0.0
                    )
                    assigned_lots.append(
                        AssignedLot(
                            lot_id=lot_counter,
                            assignment_date=date,
                            shares=quantity * multiplier,
                            basis=csp_leg.strike - credit_per_share,
                            source_group_id=group.group_id,
                        )
                    )
                    record_trade(
                        date,
                        group,
                        "ASSIGN",
                        csp_leg,
                        quantity,
                        None,
                        None,
                        0.0,
                        0.0,
                        0.0,
                        "CSP_ITM_EXPIRY",
                        {
                            "assigned_shares": quantity * multiplier,
                            "assigned_basis": csp_leg.strike - credit_per_share,
                        },
                    )
                if (
                    call_leg is not None
                    and spot > call_leg.strike
                    and quantity > 0
                ):
                    record_trade(
                        date,
                        group,
                        "ASSIGN",
                        call_leg,
                        quantity,
                        None,
                        None,
                        0.0,
                        0.0,
                        0.0,
                        "CALL_ITM_EXPIRY_ECONOMIC_EQUIVALENT",
                        {"called_away_shares": quantity * multiplier},
                    )
                groups[strategy_type] = None
                cooldown_days = int(
                    option_config[strategy_type]["cooldown_trading_days"]
                )
                cooldown_until[strategy_type] = i + cooldown_days

        base_pnl = nav_start * float(base.iloc[i]["portfolio_return"])
        nav = (
            nav_start
            + base_pnl
            + components["assigned_inventory_pnl"]
            + components["covered_call_pnl"]
            + components["cash_secured_put_pnl"]
            + components["insurance_pnl"]
        )

        decision = decision_states.iloc[i] if i > 0 else None
        p_falling = False
        p_rise_5d = np.nan
        if decision is not None and i >= 6:
            p_rise_5d = float(decision["P"]) - float(
                decision_states.iloc[i - 5]["P"]
            )
        if decision is not None and i >= 4:
            p_falling = float(decision["P"]) < float(
                decision_states.iloc[i - 3]["P"]
            )
        if decision is not None and bool(decision.get("event_exited", False)):
            call_lockout_until = max(
                call_lockout_until,
                i + int(option_config["call_lockout_after_panic_days"]),
            )
        insurance_config_for_signal = option_config["insurance"]
        if (
            decision is not None
            and bool(
                insurance_config_for_signal.get(
                    "use_insurance_score", False
                )
            )
        ):
            alert_states = set(
                insurance_config_for_signal.get(
                    "allowed_entry_states",
                    ["RISK_WARNING"],
                )
            )
            raw_insurance_alert = (
                str(decision["state"]) in alert_states
                and float(decision.get("I_need", np.nan))
                >= float(
                    insurance_config_for_signal.get(
                        "trigger_i_need_min", 50.0
                    )
                )
                and float(decision.get("I", np.nan))
                >= float(
                    insurance_config_for_signal.get(
                        "trigger_i_min", 45.0
                    )
                )
            )
            if raw_insurance_alert:
                insurance_alert_until = max(
                    insurance_alert_until,
                    i
                    + int(
                        insurance_config_for_signal.get(
                            "alert_memory_trading_days", 0
                        )
                    ),
                )

        call_group = groups["covered_call"]
        if call_group is not None:
            call_config = option_config["covered_call"]
            call_leg = call_group.legs[0]
            quote = option_quote(
                date,
                call_leg.expiry,
                call_leg.strike,
                "call",
                market,
                option_config,
                asset_symbol,
            )
            liquidation = -group_liquidation_value(date, call_group)
            entry_credit = max(call_group.entry_cash_flow, 1e-12)
            profit_fraction = (entry_credit - liquidation) / entry_credit
            state_exit = (
                decision is None
                or _call_terms(decision, option_config) is None
                or bool(decision["event_active"])
                or str(decision["state"]) == "RISK_WARNING"
            )
            close_reason = None
            if state_exit:
                close_reason = "CALL_STATE_EXIT"
            elif quote.dte <= int(call_config["close_calendar_dte"]):
                close_reason = "CALL_DTE_ROLL"
            elif (
                quote.delta >= float(call_config["itm_delta_close"])
                and quote.dte <= int(call_config["itm_delta_close_dte"])
            ):
                close_reason = "CALL_DEEP_ITM_ROLL"
            elif profit_fraction >= float(call_config["profit_take_fraction"]):
                close_reason = "CALL_PROFIT_TARGET"
            if close_reason:
                close_pnl, close_drag = close_group_quantity(
                    date,
                    call_group,
                    call_group.current_contracts,
                    close_reason,
                )
                components["covered_call_pnl"] += close_pnl
                components["execution_drag"] += close_drag
                nav += close_pnl
                groups["covered_call"] = None
                cooldown_until["covered_call"] = i + int(
                    call_config["cooldown_trading_days"]
                )

        csp_group = groups["cash_secured_put"]
        if csp_group is not None:
            put_config = option_config["cash_secured_put"]
            liquidation = -group_liquidation_value(date, csp_group)
            entry_credit = max(csp_group.entry_cash_flow, 1e-12)
            profit_fraction = (entry_credit - liquidation) / entry_credit
            if profit_fraction >= float(put_config["profit_take_fraction"]):
                close_pnl, close_drag = close_group_quantity(
                    date,
                    csp_group,
                    csp_group.current_contracts,
                    "CSP_PROFIT_TARGET",
                )
                components["cash_secured_put_pnl"] += close_pnl
                components["execution_drag"] += close_drag
                nav += close_pnl
                groups["cash_secured_put"] = None
                cooldown_until["cash_secured_put"] = i + int(
                    put_config["cooldown_trading_days"]
                )

        insurance_group = groups["insurance"]
        if insurance_group is not None and decision is not None:
            insurance_config = option_config["insurance"]
            liquidation = group_liquidation_value(date, insurance_group)
            initial_debit = max(-insurance_group.entry_cash_flow, 1e-12)
            profit_multiple = (liquidation - initial_debit) / initial_debit
            target_fraction = 0.0
            stage = insurance_group.monetization_stage
            if stage < 1 and (
                float(decision["P"])
                >= float(insurance_config["first_monetization_p"])
                or profit_multiple
                >= float(insurance_config["first_profit_multiple"])
            ):
                target_fraction = float(
                    insurance_config["first_total_close_fraction"]
                )
                insurance_group.monetization_stage = 1
            if stage < 2 and (
                float(decision["P"])
                >= float(insurance_config["second_monetization_p"])
                or str(decision["state"]) == "PANIC_STABILIZING_1"
            ):
                target_fraction = max(
                    target_fraction,
                    float(insurance_config["second_total_close_fraction"]),
                )
                insurance_group.monetization_stage = 2
            already_closed = (
                insurance_group.initial_contracts
                - insurance_group.current_contracts
            )
            target_closed = int(
                math.ceil(
                    insurance_group.initial_contracts * target_fraction
                )
            )
            if bool(
                insurance_config.get(
                    "preserve_residual_while_risk_active", False
                )
            ) and (
                bool(decision["event_active"])
                or i <= insurance_alert_until
            ):
                minimum_residual = max(
                    int(
                        math.ceil(
                            insurance_group.initial_contracts
                            * float(
                                insurance_config.get(
                                    "minimum_residual_fraction", 0.25
                                )
                            )
                        )
                    ),
                    int(
                        insurance_config.get(
                            "minimum_residual_contracts", 1
                        )
                    ),
                )
                target_closed = min(
                    target_closed,
                    max(
                        insurance_group.initial_contracts
                        - minimum_residual,
                        0,
                    ),
                )
            quantity = max(0, target_closed - already_closed)
            if quantity > 0:
                close_pnl, close_drag = close_group_quantity(
                    date,
                    insurance_group,
                    quantity,
                    "INSURANCE_PARTIAL_MONETIZATION",
                )
                components["insurance_pnl"] += close_pnl
                components["execution_drag"] += close_drag
                nav += close_pnl
            remaining = insurance_group.current_contracts
            expiry = min(leg.expiry for leg in insurance_group.legs)
            dte = int((expiry - date).days)
            held_calendar_days = int(
                (date - insurance_group.entry_date).days
            )
            minimum_hold_days = int(
                insurance_config.get("minimum_hold_calendar_days", 0)
            )
            recovery_signal = (
                float(decision["S"])
                >= float(insurance_config["recovery_s_min"])
                and p_falling
            )
            recovery_requires_event_inactive = bool(
                insurance_config.get(
                    "recovery_requires_event_inactive", False
                )
            )
            recovered = (
                recovery_signal
                and held_calendar_days >= minimum_hold_days
                and (
                    not recovery_requires_event_inactive
                    or not bool(decision["event_active"])
                )
            )
            risk_gone_days = int(
                insurance_config.get("risk_gone_confirmation_days", 1)
            )
            recent_p = decision_states["P"].iloc[
                max(0, i - risk_gone_days + 1) : i + 1
            ].dropna()
            risk_gone_p_max = float(
                insurance_config.get("risk_gone_p_max", 30.0)
            )
            risk_gone = (
                str(decision["state"])
                in {
                    "NORMAL_PARTICIPATION",
                    "RANGE_CARRY",
                    "GREED_TREND",
                    "GREED_EXHAUSTING",
                }
                and not bool(decision["event_active"])
                and held_calendar_days >= minimum_hold_days
                and len(recent_p) >= risk_gone_days
                and float(recent_p.max()) < risk_gone_p_max
            )
            if remaining > 0 and (
                recovered
                or risk_gone
                or dte <= int(insurance_config["close_calendar_dte"])
            ):
                reason = (
                    "INSURANCE_RECOVERY_EXIT"
                    if recovered or risk_gone
                    else "INSURANCE_DTE_ROLL"
                )
                close_pnl, close_drag = close_group_quantity(
                    date,
                    insurance_group,
                    remaining,
                    reason,
                )
                components["insurance_pnl"] += close_pnl
                components["execution_drag"] += close_drag
                nav += close_pnl
            if insurance_group.current_contracts == 0:
                groups["insurance"] = None
                cooldown_until["insurance"] = i + int(
                    insurance_config["cooldown_trading_days"]
                )

        if decision is not None and assigned_lots:
            assigned_config = option_config["assigned_inventory"]
            planned_weight = float(base.iloc[i]["executed_asset_weight"])
            maximum_assigned_value = max(
                0.0,
                (1.0 - min_free_cash - planned_weight) * nav,
            )
            current_assigned_value = sum(
                lot.shares * spot for lot in assigned_lots
            )
            survivors: list[AssignedLot] = []
            for lot in assigned_lots:
                greed_exit = (
                    float(decision["G"])
                    >= float(assigned_config["greed_exit_g_min"])
                    and float(decision["E"])
                    >= float(assigned_config["greed_exit_e_min"])
                    and spot
                    >= lot.basis
                    * (1.0 + float(assigned_config["minimum_gain_to_exit"]))
                )
                timed_exit = (
                    lot.holding_days
                    >= int(
                        assigned_config["maximum_holding_trading_days"]
                    )
                    and spot >= lot.basis
                )
                capacity_exit = current_assigned_value > maximum_assigned_value
                if greed_exit or timed_exit or capacity_exit:
                    sale_value = lot.shares * spot
                    sale_cost = sale_value * transaction_cost_rate
                    nav -= sale_cost
                    components["assigned_inventory_pnl"] -= sale_cost
                    components["execution_drag"] += sale_cost
                    current_assigned_value -= sale_value
                    dummy_group = OptionGroup(
                        group_id=lot.source_group_id,
                        strategy_type="assigned_inventory",
                        entry_date=lot.assignment_date,
                        entry_spot=lot.basis,
                        initial_contracts=lot.shares // multiplier,
                        current_contracts=0,
                        entry_cash_flow=0.0,
                        entry_unit_cash_flow=0.0,
                        entry_commission=0.0,
                    )
                    record_trade(
                        date,
                        dummy_group,
                        "SELL_ASSIGNED",
                        None,
                        lot.shares // multiplier,
                        None,
                        spot,
                        sale_value,
                        -sale_cost,
                        sale_cost,
                        (
                            "ASSIGNED_GREED_EXIT"
                            if greed_exit
                            else (
                                "ASSIGNED_TIME_EXIT"
                                if timed_exit
                                else "ASSIGNED_CAPACITY_EXIT"
                            )
                        ),
                        {
                            "assigned_shares": lot.shares,
                            "assigned_basis": lot.basis,
                        },
                    )
                else:
                    survivors.append(lot)
            assigned_lots = survivors

        planned_weight = float(base.iloc[i]["executed_asset_weight"])
        base_shares = max(0.0, nav * planned_weight / max(spot, 1e-12))
        assigned_shares = sum(lot.shares for lot in assigned_lots)
        total_shares = base_shares + assigned_shares

        if (
            enabled["insurance"]
            and bool(option_config["insurance"]["enabled"])
            and groups["insurance"] is None
            and groups["covered_call"] is None
            and decision is not None
            and i > cooldown_until["insurance"]
        ):
            insurance_config = option_config["insurance"]
            use_insurance_score = bool(
                insurance_config.get("use_insurance_score", False)
            )
            if use_insurance_score:
                trigger = (
                    i <= insurance_alert_until
                    and float(
                        decision.get("I_affordability", np.nan)
                    )
                    >= float(
                        insurance_config.get(
                            "trigger_i_affordability_min", 10.0
                        )
                    )
                    and float(
                        decision.get("R_put", decision["R_proxy"])
                    )
                    <= float(insurance_config["entry_r_proxy_max"])
                )
            else:
                trigger = (
                    str(decision["state"]) == "RISK_WARNING"
                    and float(decision["P"])
                    >= float(insurance_config["trigger_p_min"])
                    and float(decision["S"])
                    < float(insurance_config["trigger_s_max"])
                    and pd.notna(p_rise_5d)
                    and p_rise_5d
                    >= float(insurance_config["trigger_p_rise_5d"])
                    and float(
                        decision.get("R_put", decision["R_proxy"])
                    )
                    <= float(insurance_config["entry_r_proxy_max"])
                )
            if trigger:
                expiry = choose_expiry(
                    date,
                    index,
                    int(insurance_config["target_calendar_dte"]),
                    int(insurance_config["minimum_calendar_dte"]),
                    int(insurance_config["maximum_calendar_dte"]),
                )
                selected = (
                    choose_strike_for_delta(
                        date,
                        expiry,
                        float(insurance_config["long_put_target_delta"]),
                        "put",
                        market,
                        option_config,
                        asset_symbol,
                    )
                    if expiry is not None
                    else None
                )
                if selected:
                    long_strike, long_quote = selected
                    leg_specs = [
                        ("put", 1, long_strike, expiry, long_quote)
                    ]
                    unit_debit = long_quote.ask * multiplier + commission
                    selected_structure = str(
                        insurance_config["structure"]
                    )
                    if selected_structure == "adaptive":
                        protective_need_min = float(
                            insurance_config.get(
                                "adaptive_protective_i_need_min", 70.0
                            )
                        )
                        protective_affordability_min = float(
                            insurance_config.get(
                                "adaptive_protective_affordability_min",
                                20.0,
                            )
                        )
                        if (
                            float(decision.get("I_need", 0.0))
                            >= protective_need_min
                            and float(
                                decision.get("I_affordability", 0.0)
                            )
                            >= protective_affordability_min
                        ):
                            selected_structure = "protective_put"
                        else:
                            selected_structure = "put_spread"
                    if selected_structure == "put_spread":
                        short_selected = choose_strike_for_delta(
                            date,
                            expiry,
                            float(
                                insurance_config[
                                    "short_put_target_delta"
                                ]
                            ),
                            "put",
                            market,
                            option_config,
                            asset_symbol,
                        )
                        if (
                            short_selected
                            and short_selected[0] < long_strike
                        ):
                            short_strike, short_quote = short_selected
                            leg_specs.append(
                                (
                                    "put",
                                    -1,
                                    short_strike,
                                    expiry,
                                    short_quote,
                                )
                            )
                            unit_debit -= (
                                short_quote.bid * multiplier - commission
                            )
                    coverage_fraction = float(
                        insurance_config["coverage_fraction"]
                    )
                    if (
                        use_insurance_score
                        and bool(
                            insurance_config.get(
                                "dynamic_coverage_enabled", False
                            )
                        )
                    ):
                        need = float(decision.get("I_need", 0.0))
                        if need >= float(
                            insurance_config.get(
                                "high_need_threshold", 75.0
                            )
                        ):
                            coverage_fraction = float(
                                insurance_config.get(
                                    "high_need_coverage_fraction",
                                    coverage_fraction,
                                )
                            )
                        elif need >= float(
                            insurance_config.get(
                                "medium_need_threshold", 60.0
                            )
                        ):
                            coverage_fraction = float(
                                insurance_config.get(
                                    "medium_need_coverage_fraction",
                                    coverage_fraction,
                                )
                            )
                        else:
                            coverage_fraction = float(
                                insurance_config.get(
                                    "low_need_coverage_fraction",
                                    coverage_fraction,
                                )
                            )
                    target_contracts = int(
                        math.floor(
                            total_shares
                            * coverage_fraction
                            / multiplier
                        )
                    )
                    maximum_debit = (
                        nav
                        * float(
                            insurance_config["maximum_net_debit_nav"]
                        )
                    )
                    year_budget = (
                        nav
                        * float(
                            insurance_config["annual_gross_debit_nav"]
                        )
                        - annual_insurance_debit.get(date.year, 0.0)
                    )
                    budget = max(0.0, min(maximum_debit, year_budget))
                    budget_contracts = int(
                        math.floor(budget / max(unit_debit, 1e-12))
                    )
                    contracts = min(target_contracts, budget_contracts)
                    if contracts > 0 and long_quote.ask >= minimum_price:
                        group, open_pnl, open_drag, premiums = open_group(
                            date,
                            "insurance",
                            leg_specs,
                            contracts,
                            0.0,
                            (
                                "INSURANCE_MODEL_ENTRY_"
                                + selected_structure.upper()
                                if use_insurance_score
                                else "RISK_WARNING_INSURANCE_ENTRY"
                            ),
                        )
                        groups["insurance"] = group
                        components["insurance_pnl"] += open_pnl
                        components["execution_drag"] += open_drag
                        for key, value in premiums.items():
                            components[key] += value
                        nav += open_pnl
                        annual_insurance_debit[date.year] = (
                            annual_insurance_debit.get(date.year, 0.0)
                            + max(-group.entry_cash_flow, 0.0)
                        )

        if (
            enabled["cash_secured_put"]
            and bool(option_config["cash_secured_put"]["enabled"])
            and groups["cash_secured_put"] is None
            and groups["insurance"] is None
            and decision is not None
            and i > cooldown_until["cash_secured_put"]
        ):
            terms = _csp_terms(decision, option_config)
            if terms:
                bucket_fraction, target_delta, minimum_discount = terms
                put_config = option_config["cash_secured_put"]
                expiry = choose_expiry(
                    date,
                    index,
                    int(put_config["target_calendar_dte"]),
                    int(put_config["minimum_calendar_dte"]),
                    int(put_config["maximum_calendar_dte"]),
                )
                selected = (
                    choose_strike_for_delta(
                        date,
                        expiry,
                        target_delta,
                        "put",
                        market,
                        option_config,
                        asset_symbol,
                    )
                    if expiry is not None
                    else None
                )
                if selected:
                    strike, quote = selected
                    step = _strike_step(spot)
                    while (
                        strike - quote.bid
                        > spot * (1.0 - minimum_discount)
                        and strike > step
                    ):
                        strike -= step
                        quote = option_quote(
                            date,
                            expiry,
                            strike,
                            "put",
                            market,
                            option_config,
                            asset_symbol,
                        )
                    current_collateral = sum(
                        group.collateral
                        for group in groups.values()
                        if group is not None
                    )
                    assigned_value = assigned_shares * spot
                    bucket_capacity = max(
                        0.0,
                        nav
                        * float(capital_split["put_bucket"])
                        * bucket_fraction
                        - assigned_value,
                    )
                    free_cash_capacity = max(
                        0.0,
                        nav
                        * (1.0 - planned_weight - min_free_cash)
                        - assigned_value
                        - current_collateral,
                    )
                    capacity = min(bucket_capacity, free_cash_capacity)
                    collateral_per_contract = strike * multiplier
                    contracts = int(
                        math.floor(
                            capacity / max(collateral_per_contract, 1e-12)
                        )
                    )
                    if (
                        contracts > 0
                        and quote.bid >= minimum_price
                        and strike - quote.bid
                        <= spot * (1.0 - minimum_discount)
                    ):
                        collateral = collateral_per_contract * contracts
                        group, open_pnl, open_drag, premiums = open_group(
                            date,
                            "cash_secured_put",
                            [("put", -1, strike, expiry, quote)],
                            contracts,
                            collateral,
                            "CONDITIONAL_ENTRY_CSP",
                        )
                        groups["cash_secured_put"] = group
                        components["cash_secured_put_pnl"] += open_pnl
                        components["execution_drag"] += open_drag
                        for key, value in premiums.items():
                            components[key] += value
                        nav += open_pnl

        if (
            enabled["covered_call"]
            and bool(option_config["covered_call"]["enabled"])
            and groups["covered_call"] is None
            and groups["insurance"] is None
            and decision is not None
            and i > cooldown_until["covered_call"]
            and i > call_lockout_until
        ):
            terms = _call_terms(decision, option_config)
            if terms:
                coverage, target_delta, minimum_upside = terms
                call_config = option_config["covered_call"]
                contracts = int(
                    math.floor(total_shares * coverage / multiplier)
                )
                expiry = choose_expiry(
                    date,
                    index,
                    int(call_config["target_calendar_dte"]),
                    int(call_config["minimum_calendar_dte"]),
                    int(call_config["maximum_calendar_dte"]),
                )
                selected = (
                    choose_strike_for_delta(
                        date,
                        expiry,
                        target_delta,
                        "call",
                        market,
                        option_config,
                        asset_symbol,
                    )
                    if expiry is not None
                    else None
                )
                if contracts > 0 and selected:
                    strike, quote = selected
                    step = _strike_step(spot)
                    while (
                        strike + quote.bid
                        < spot * (1.0 + minimum_upside)
                    ):
                        strike += step
                        quote = option_quote(
                            date,
                            expiry,
                            strike,
                            "call",
                            market,
                            option_config,
                            asset_symbol,
                        )
                        if strike > spot * 1.50:
                            break
                    if (
                        quote.bid >= minimum_price
                        and strike + quote.bid
                        >= spot * (1.0 + minimum_upside)
                    ):
                        group, open_pnl, open_drag, premiums = open_group(
                            date,
                            "covered_call",
                            [("call", -1, strike, expiry, quote)],
                            contracts,
                            0.0,
                            "GREED_EXHAUSTION_COVERED_CALL",
                        )
                        groups["covered_call"] = group
                        components["covered_call_pnl"] += open_pnl
                        components["execution_drag"] += open_drag
                        for key, value in premiums.items():
                            components[key] += value
                        nav += open_pnl

        current_call_contracts = (
            groups["covered_call"].current_contracts
            if groups["covered_call"] is not None
            else 0
        )
        current_csp_collateral = (
            groups["cash_secured_put"].collateral
            if groups["cash_secured_put"] is not None
            else 0.0
        )
        current_insurance_contracts = (
            groups["insurance"].current_contracts
            if groups["insurance"] is not None
            else 0
        )
        portfolio_return = nav / nav_start - 1.0
        row = {
            "date": date,
            "strategy_name": strategy_name or asset_symbol,
            "state": base.iloc[i].get("state", None),
            "event_id": base.iloc[i].get("event_id", 0),
            "event_active": base.iloc[i].get("event_active", False),
            "P": base.iloc[i].get("P", np.nan),
            "S": base.iloc[i].get("S", np.nan),
            "G": base.iloc[i].get("G", np.nan),
            "E": base.iloc[i].get("E", np.nan),
            "R_proxy": base.iloc[i].get("R_proxy", np.nan),
            "R_put": base.iloc[i].get(
                "R_put", base.iloc[i].get("R_proxy", np.nan)
            ),
            "R_call": base.iloc[i].get(
                "R_call", base.iloc[i].get("R_proxy", np.nan)
            ),
            "I": base.iloc[i].get("I", np.nan),
            "I_need": base.iloc[i].get("I_need", np.nan),
            "I_affordability": base.iloc[i].get(
                "I_affordability", np.nan
            ),
            "target_asset_weight": base.iloc[i]["target_asset_weight"],
            "executed_asset_weight": base.iloc[i]["executed_asset_weight"],
            "asset_return": base.iloc[i]["asset_return"],
            "cash_return": base.iloc[i]["cash_return"],
            "turnover": base.iloc[i]["turnover"],
            "transaction_cost": base.iloc[i]["transaction_cost"],
            "base_portfolio_return": base.iloc[i]["portfolio_return"],
            "base_pnl": base_pnl,
            **components,
            "assigned_shares": assigned_shares,
            "assigned_market_value": assigned_shares * spot,
            "assigned_weight": assigned_shares * spot / max(nav, 1e-12),
            "covered_call_contracts": current_call_contracts,
            "covered_call_coverage": (
                current_call_contracts * multiplier / max(total_shares, 1e-12)
            ),
            "cash_secured_put_collateral": current_csp_collateral,
            "cash_secured_put_collateral_weight": (
                current_csp_collateral / max(nav, 1e-12)
            ),
            "insurance_contracts": current_insurance_contracts,
            "portfolio_return": portfolio_return,
            "equity": nav,
        }
        row["effective_asset_weight"] = min(
            1.0,
            float(row["executed_asset_weight"]) + float(row["assigned_weight"]),
        )
        daily_rows.append(row)

    frame = pd.DataFrame(daily_rows).set_index("date")
    frame["gross_return"] = frame["portfolio_return"]
    frame["drawdown"] = frame["equity"] / frame["equity"].cummax() - 1.0
    trades = pd.DataFrame(trade_rows)
    if not trades.empty:
        trades["date"] = pd.to_datetime(trades["date"])
        if "expiry" in trades:
            trades["expiry"] = pd.to_datetime(trades["expiry"])

    call_assignments = (
        int(
            (
                (trades["action"] == "ASSIGN")
                & (trades["strategy_type"] == "covered_call")
            ).sum()
        )
        if not trades.empty
        else 0
    )
    csp_assignments = (
        int(
            (
                (trades["action"] == "ASSIGN")
                & (trades["strategy_type"] == "cash_secured_put")
            ).sum()
        )
        if not trades.empty
        else 0
    )
    call_closures = (
        trades[
            (trades["strategy_type"] == "covered_call")
            & (trades["action"].isin(["CLOSE", "SETTLE"]))
        ]
        if not trades.empty
        else pd.DataFrame()
    )
    call_cap_intrinsic = (
        float(
            (
                (call_closures["spot"] - call_closures["strike"])
                .clip(lower=0.0)
                * call_closures["contracts"]
                * multiplier
            ).sum()
        )
        if not call_closures.empty
        else 0.0
    )
    insurance_net_entry_debit = float(
        frame["insurance_long_premium_paid"].sum()
        - frame["insurance_short_premium_collected"].sum()
    )
    summary = {
        "strategy": strategy_name or asset_symbol,
        "asset": asset_symbol,
        "initial_nav": float(config["strategy"]["initial_nav"]),
        "ending_nav": float(frame["equity"].iloc[-1]),
        "covered_call_net_pnl": float(frame["covered_call_pnl"].sum()),
        "cash_secured_put_net_pnl": float(
            frame["cash_secured_put_pnl"].sum()
        ),
        "insurance_net_pnl": float(frame["insurance_pnl"].sum()),
        "assigned_inventory_net_pnl": float(
            frame["assigned_inventory_pnl"].sum()
        ),
        "execution_drag": float(frame["execution_drag"].sum()),
        "call_premium_collected": float(
            frame["call_premium_collected"].sum()
        ),
        "csp_premium_collected": float(
            frame["csp_premium_collected"].sum()
        ),
        "insurance_long_premium_paid": float(
            frame["insurance_long_premium_paid"].sum()
        ),
        "insurance_short_premium_collected": float(
            frame["insurance_short_premium_collected"].sum()
        ),
        "insurance_net_entry_debit": insurance_net_entry_debit,
        "insurance_pnl_during_panic": float(
            frame.loc[
                frame["event_active"].astype(bool)
                | frame["P"].ge(50.0),
                "insurance_pnl",
            ].sum()
        ),
        "call_intrinsic_cap_cost": call_cap_intrinsic,
        "covered_call_entry_groups": int(
            (
                (trades["action"] == "OPEN")
                & (trades["strategy_type"] == "covered_call")
            ).sum()
        )
        if not trades.empty
        else 0,
        "csp_entry_groups": int(
            (
                (trades["action"] == "OPEN")
                & (trades["strategy_type"] == "cash_secured_put")
            ).sum()
        )
        if not trades.empty
        else 0,
        "insurance_entry_groups": int(
            trades[
                (trades["action"] == "OPEN")
                & (trades["strategy_type"] == "insurance")
            ]["group_id"].nunique()
        )
        if not trades.empty
        else 0,
        "call_assignments": call_assignments,
        "csp_assignments": csp_assignments,
        "average_call_coverage": float(
            frame["covered_call_coverage"].mean()
        ),
        "average_csp_collateral_weight": float(
            frame["cash_secured_put_collateral_weight"].mean()
        ),
        "average_assigned_weight": float(frame["assigned_weight"].mean()),
        "proxy_iv_multiplier": float(option_config["iv_multiplier"]),
        "proxy_spread_multiplier": float(
            option_config["spread_multiplier"]
        ),
        "insurance_structure": str(
            option_config["insurance"]["structure"]
        ),
    }
    return frame, trades, summary
