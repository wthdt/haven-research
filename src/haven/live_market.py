from __future__ import annotations

import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import (
    _cached_bytes,
    fetch_nasdaq_history,
)
from .options_proxy import black_scholes_price_delta


NASDAQ100_MEMBERS_URL = (
    "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
)
NASDAQ_OPTION_CHAIN_URL_TEMPLATE = (
    "https://api.nasdaq.com/api/quote/{symbol}/option-chain"
)


def _safe_number(value: Any) -> float:
    if value is None:
        return np.nan
    text = str(value).replace("$", "").replace(",", "").strip()
    if text in {"", "--", "N/A", "NA", "UNCH", "null", "None"}:
        return np.nan
    try:
        return float(text.rstrip("%"))
    except ValueError:
        return np.nan


def fetch_nasdaq100_members(
    cache_dir: Path,
    *,
    force: bool = True,
) -> tuple[pd.DataFrame, pd.Timestamp]:
    path = cache_dir / "nasdaq_nasdaq100_members_latest.json"
    payload = json.loads(
        _cached_bytes(NASDAQ100_MEMBERS_URL, path, force).decode("utf-8")
    )
    outer = payload.get("data") or {}
    inner = outer.get("data") or {}
    rows = inner.get("rows") or []
    if not rows:
        raise RuntimeError("Nasdaq did not return Nasdaq-100 members")
    frame = pd.DataFrame(rows)
    frame["symbol"] = frame["symbol"].astype(str).str.strip()
    frame["market_cap"] = frame.get("marketCap", np.nan).map(_safe_number)
    as_of = pd.to_datetime(outer.get("date"), errors="coerce")
    if pd.isna(as_of):
        raise RuntimeError("Nasdaq member list has no valid as-of date")
    return frame, pd.Timestamp(as_of).normalize()


def _member_breadth_row(
    symbol: str,
    history: pd.DataFrame,
    as_of: pd.Timestamp,
) -> dict[str, Any]:
    section = history.loc[:as_of].dropna(subset=["close"]).copy()
    if section.empty:
        return {"symbol": symbol, "status": "NO_DATA"}
    close = section["close"].astype(float)
    last = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) >= 2 else np.nan
    row: dict[str, Any] = {
        "symbol": symbol,
        "status": "OK",
        "date": str(close.index[-1].date()),
        "close": last,
        "return_1d": (
            last / previous - 1.0
            if np.isfinite(previous) and previous > 0.0
            else np.nan
        ),
        "above_ma20": (
            bool(last > float(close.tail(20).mean()))
            if len(close) >= 20
            else np.nan
        ),
        "above_ma50": (
            bool(last > float(close.tail(50).mean()))
            if len(close) >= 50
            else np.nan
        ),
        "above_ma200": (
            bool(last > float(close.tail(200).mean()))
            if len(close) >= 200
            else np.nan
        ),
        "new_high_252": (
            bool(last >= float(close.iloc[:-1].tail(252).max()))
            if len(close) >= 253
            else np.nan
        ),
        "new_low_252": (
            bool(last <= float(close.iloc[:-1].tail(252).min()))
            if len(close) >= 253
            else np.nan
        ),
        "observations": int(len(close)),
    }
    return row


def summarize_breadth_rows(
    rows: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    minimum_member_coverage: float = 0.85,
) -> dict[str, Any]:
    valid = rows[rows["status"].eq("OK")].copy()
    total = int(len(rows))
    valid_count = int(len(valid))

    def share(column: str) -> tuple[float, float]:
        values = valid[column].dropna()
        coverage = len(values) / max(total, 1)
        score = float(values.astype(float).mean() * 100.0) if len(values) else np.nan
        return score, coverage

    above20, cov20 = share("above_ma20")
    above50, cov50 = share("above_ma50")
    above200, cov200 = share("above_ma200")
    highs, cov_high = share("new_high_252")
    lows, cov_low = share("new_low_252")
    returns = valid["return_1d"].dropna()
    advances = int((returns > 0.0).sum())
    declines = int((returns < 0.0).sum())
    unchanged = int((returns == 0.0).sum())
    ad_coverage = len(returns) / max(total, 1)
    breadth_coverage = min(
        valid_count / max(total, 1),
        cov20,
        cov50,
        cov200,
        ad_coverage,
    )
    status = (
        "LIVE_SHADOW_READY"
        if breadth_coverage >= minimum_member_coverage
        else "LIVE_SHADOW_DATA_GUARD"
    )
    return {
        "as_of": str(as_of.date()),
        "status": status,
        "member_count": total,
        "valid_member_count": valid_count,
        "coverage": float(breadth_coverage),
        "percent_above_ma20": above20,
        "percent_above_ma50": above50,
        "percent_above_ma200": above200,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_decline_net": advances - declines,
        "advance_decline_ratio": (
            advances / max(declines, 1)
            if len(returns)
            else np.nan
        ),
        "percent_new_high_252": highs,
        "percent_new_low_252": lows,
        "high_low_net": (
            highs - lows
            if np.isfinite(highs) and np.isfinite(lows)
            else np.nan
        ),
        "field_coverage": {
            "above_ma20": cov20,
            "above_ma50": cov50,
            "above_ma200": cov200,
            "advance_decline": ad_coverage,
            "new_high": cov_high,
            "new_low": cov_low,
        },
        "historical_backtest_eligible": False,
        "survivorship_note": (
            "Current members are valid for the current snapshot only. They "
            "must not be used to reconstruct historical constituent breadth."
        ),
    }


def build_live_breadth_snapshot(
    cache_dir: Path,
    *,
    force: bool = True,
    max_workers: int = 8,
    minimum_member_coverage: float = 0.85,
) -> tuple[dict[str, Any], pd.DataFrame]:
    members, as_of = fetch_nasdaq100_members(cache_dir, force=force)
    start = str((as_of - pd.Timedelta(days=430)).date())
    end = str(as_of.date())
    histories: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}

    def load(symbol: str) -> tuple[str, pd.DataFrame]:
        history = fetch_nasdaq_history(
            symbol,
            start,
            end,
            cache_dir / "nasdaq100_members",
            force=force,
            asset_class="stocks",
        )
        return symbol, history

    symbols = members["symbol"].dropna().astype(str).tolist()
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {pool.submit(load, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                loaded_symbol, history = future.result()
                histories[loaded_symbol] = history
            except Exception as exc:  # pragma: no cover - network path
                errors[symbol] = f"{type(exc).__name__}: {exc}"

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        if symbol not in histories:
            rows.append(
                {
                    "symbol": symbol,
                    "status": "DOWNLOAD_ERROR",
                    "error": errors.get(symbol, "unknown"),
                }
            )
            continue
        rows.append(_member_breadth_row(symbol, histories[symbol], as_of))
    detail = pd.DataFrame(rows)
    summary = summarize_breadth_rows(
        detail,
        as_of=as_of,
        minimum_member_coverage=minimum_member_coverage,
    )
    summary["download_error_count"] = int(len(errors))
    return summary, detail


def fetch_nasdaq_option_chain(
    symbol: str,
    cache_dir: Path,
    *,
    force: bool = True,
    asset_class: str = "etf",
    horizon_days: int = 140,
) -> tuple[dict[str, Any], pd.DataFrame]:
    normalized_symbol = str(symbol).strip().upper()
    if not re.fullmatch(r"[A-Z0-9.^-]{1,16}", normalized_symbol):
        raise ValueError(f"Invalid Nasdaq option symbol: {symbol!r}")
    request_start = pd.Timestamp.now(
        tz="America/New_York"
    ).normalize()
    request_end = request_start + pd.Timedelta(days=int(horizon_days))
    params = urllib.parse.urlencode(
        {
            "assetclass": str(asset_class),
            "fromdate": str(request_start.date()),
            "todate": str(request_end.date()),
            "callput": "callput",
            "money": "all",
            "type": "all",
            "limit": 5000,
        }
    )
    base_url = NASDAQ_OPTION_CHAIN_URL_TEMPLATE.format(
        symbol=normalized_symbol
    )
    url = f"{base_url}?{params}"
    path = (
        cache_dir
        / f"nasdaq_{normalized_symbol.lower()}_option_chain_latest.json"
    )
    payload = json.loads(
        _cached_bytes(url, path, force).decode("utf-8")
    )
    data = payload.get("data") or {}
    rows = ((data.get("table") or {}).get("rows") or [])
    if not rows:
        raise RuntimeError(
            f"Nasdaq did not return the {normalized_symbol} option chain"
        )

    last_trade = str(data.get("lastTrade") or "")
    spot_match = re.search(r"\$([\d,.]+)", last_trade)
    date_match = re.search(
        r"AS OF ([A-Z]{3} \d{1,2}, \d{4})",
        last_trade.upper(),
    )
    spot = _safe_number(spot_match.group(1) if spot_match else None)
    as_of = pd.to_datetime(
        date_match.group(1) if date_match else None,
        errors="coerce",
    )
    if not np.isfinite(spot) or pd.isna(as_of):
        raise RuntimeError(
            f"{normalized_symbol} chain is missing spot or as-of date"
        )

    parsed: list[dict[str, Any]] = []
    current_expiry: pd.Timestamp | None = None
    for row in rows:
        if row.get("expirygroup"):
            parsed_expiry = pd.to_datetime(
                row["expirygroup"], errors="coerce"
            )
            if pd.notna(parsed_expiry):
                current_expiry = pd.Timestamp(parsed_expiry).normalize()
            continue
        strike = _safe_number(row.get("strike"))
        if not np.isfinite(strike) or current_expiry is None:
            continue
        for prefix, option_type in [("c", "call"), ("p", "put")]:
            parsed.append(
                {
                    "symbol": normalized_symbol,
                    "as_of": pd.Timestamp(as_of).normalize(),
                    "spot": spot,
                    "expiry": current_expiry,
                    "option_type": option_type,
                    "strike": strike,
                    "last": _safe_number(row.get(f"{prefix}_Last")),
                    "bid": _safe_number(row.get(f"{prefix}_Bid")),
                    "ask": _safe_number(row.get(f"{prefix}_Ask")),
                    "volume": _safe_number(row.get(f"{prefix}_Volume")),
                    "open_interest": _safe_number(
                        row.get(f"{prefix}_Openinterest")
                    ),
                }
            )
    chain = pd.DataFrame(parsed)
    metadata = {
        "symbol": normalized_symbol,
        "as_of": str(pd.Timestamp(as_of).date()),
        "spot": float(spot),
        "contracts": int(len(chain)),
        "source": (
            f"Nasdaq delayed {normalized_symbol} option-chain snapshot"
        ),
        "historical_backtest_eligible": False,
    }
    return metadata, chain


def fetch_nasdaq_qqq_option_chain(
    cache_dir: Path,
    *,
    force: bool = True,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Backward-compatible wrapper for the v0.4 QQQ live shadow."""

    return fetch_nasdaq_option_chain(
        "QQQ",
        cache_dir,
        force=force,
        asset_class="etf",
    )


def implied_volatility(
    market_price: float,
    *,
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    dividend_yield: float,
    option_type: str,
    lower: float = 0.005,
    upper: float = 5.0,
) -> float:
    if (
        not np.isfinite(market_price)
        or market_price <= 0.0
        or time_years <= 0.0
    ):
        return np.nan

    def value(volatility: float) -> float:
        return black_scholes_price_delta(
            spot,
            strike,
            time_years,
            rate,
            dividend_yield,
            volatility,
            option_type,
        )[0]

    low_value = value(lower)
    high_value = value(upper)
    tolerance = max(1e-6, market_price * 1e-7)
    if market_price < low_value - tolerance or market_price > high_value + tolerance:
        return np.nan
    low = lower
    high = upper
    for _ in range(100):
        middle = (low + high) / 2.0
        middle_value = value(middle)
        if abs(middle_value - market_price) <= tolerance:
            return float(middle)
        if middle_value < market_price:
            low = middle
        else:
            high = middle
    return float((low + high) / 2.0)


def enrich_option_chain(
    chain: pd.DataFrame,
    *,
    rate: float,
    dividend_yield: float,
) -> pd.DataFrame:
    result = chain.copy()
    result["dte"] = (
        pd.to_datetime(result["expiry"])
        - pd.to_datetime(result["as_of"])
    ).dt.days
    both = result["bid"].notna() & result["ask"].notna()
    result["mid"] = np.where(
        both,
        (result["bid"] + result["ask"]) / 2.0,
        result["last"],
    )
    result["relative_spread"] = np.where(
        both & result["mid"].gt(0.0),
        (result["ask"] - result["bid"]) / result["mid"],
        np.nan,
    )

    ivs: list[float] = []
    deltas: list[float] = []
    for row in result.itertuples(index=False):
        iv = implied_volatility(
            float(row.mid),
            spot=float(row.spot),
            strike=float(row.strike),
            time_years=max(float(row.dte), 0.0) / 365.0,
            rate=float(rate),
            dividend_yield=float(dividend_yield),
            option_type=str(row.option_type),
        )
        ivs.append(iv)
        if np.isfinite(iv):
            delta = black_scholes_price_delta(
                float(row.spot),
                float(row.strike),
                max(float(row.dte), 0.0) / 365.0,
                float(rate),
                float(dividend_yield),
                iv,
                str(row.option_type),
            )[1]
        else:
            delta = np.nan
        deltas.append(delta)
    result["implied_volatility"] = ivs
    result["delta"] = deltas
    return result


def _nearest_expiry(
    chain: pd.DataFrame,
    target_dte: int,
    *,
    minimum_dte: int,
    maximum_dte: int,
) -> int | None:
    dtes = sorted(
        int(value)
        for value in chain["dte"].dropna().unique()
        if minimum_dte <= int(value) <= maximum_dte
    )
    return min(dtes, key=lambda value: abs(value - target_dte)) if dtes else None


def _expiry_metrics(
    chain: pd.DataFrame,
    dte: int | None,
    *,
    spot: float,
    target_put_delta: float,
) -> dict[str, Any]:
    if dte is None:
        return {
            "dte": None,
            "atm_iv": np.nan,
            "put_25d_iv": np.nan,
            "call_25d_iv": np.nan,
            "put_skew_vol_points": np.nan,
            "call_skew_vol_points": np.nan,
        }
    section = chain[chain["dte"].eq(dte)].copy()
    valid = section.dropna(subset=["implied_volatility"]).copy()
    if valid.empty:
        return _expiry_metrics(
            chain.iloc[0:0],
            None,
            spot=spot,
            target_put_delta=target_put_delta,
        )
    valid["moneyness_distance"] = (valid["strike"] / spot - 1.0).abs()
    atm_legs = (
        valid.sort_values("moneyness_distance")
        .groupby("option_type", as_index=False)
        .head(1)
    )
    atm_iv = float(atm_legs["implied_volatility"].mean())

    puts = valid[
        valid["option_type"].eq("put")
        & valid["delta"].between(-0.60, -0.02)
    ].copy()
    calls = valid[
        valid["option_type"].eq("call")
        & valid["delta"].between(0.02, 0.60)
    ].copy()
    put_iv = np.nan
    call_iv = np.nan
    if not puts.empty:
        put_index = (puts["delta"].abs() - target_put_delta).abs().idxmin()
        put_iv = float(puts.loc[put_index, "implied_volatility"])
    if not calls.empty:
        call_index = (
            calls["delta"].abs() - target_put_delta
        ).abs().idxmin()
        call_iv = float(calls.loc[call_index, "implied_volatility"])
    return {
        "dte": int(dte),
        "atm_iv": atm_iv,
        "put_25d_iv": put_iv,
        "call_25d_iv": call_iv,
        "put_skew_vol_points": (
            (put_iv - atm_iv) * 100.0
            if np.isfinite(put_iv) and np.isfinite(atm_iv)
            else np.nan
        ),
        "call_skew_vol_points": (
            (call_iv - atm_iv) * 100.0
            if np.isfinite(call_iv) and np.isfinite(atm_iv)
            else np.nan
        ),
    }


def summarize_option_chain(
    chain: pd.DataFrame,
    *,
    rate: float,
    dividend_yield: float,
    target_dte_short: int = 30,
    target_dte_long: int = 90,
    target_put_delta: float = 0.25,
) -> tuple[dict[str, Any], pd.DataFrame]:
    enriched = enrich_option_chain(
        chain,
        rate=rate,
        dividend_yield=dividend_yield,
    )
    spot = float(enriched["spot"].dropna().iloc[0])
    short_dte = _nearest_expiry(
        enriched,
        target_dte_short,
        minimum_dte=14,
        maximum_dte=55,
    )
    long_dte = _nearest_expiry(
        enriched,
        target_dte_long,
        minimum_dte=56,
        maximum_dte=125,
    )
    short = _expiry_metrics(
        enriched,
        short_dte,
        spot=spot,
        target_put_delta=target_put_delta,
    )
    long = _expiry_metrics(
        enriched,
        long_dte,
        spot=spot,
        target_put_delta=target_put_delta,
    )

    liquid = enriched[
        enriched["dte"].between(7, 125)
        & enriched["strike"].between(spot * 0.70, spot * 1.30)
    ].copy()
    spread_sample = liquid[
        liquid["mid"].ge(0.05)
        & liquid["delta"].abs().between(0.05, 0.95)
    ]["relative_spread"].dropna()
    put_volume = float(
        liquid.loc[liquid["option_type"].eq("put"), "volume"]
        .fillna(0.0)
        .sum()
    )
    call_volume = float(
        liquid.loc[liquid["option_type"].eq("call"), "volume"]
        .fillna(0.0)
        .sum()
    )
    put_oi = float(
        liquid.loc[liquid["option_type"].eq("put"), "open_interest"]
        .fillna(0.0)
        .sum()
    )
    call_oi = float(
        liquid.loc[liquid["option_type"].eq("call"), "open_interest"]
        .fillna(0.0)
        .sum()
    )
    required = [
        short["atm_iv"],
        short["put_25d_iv"],
        long["atm_iv"],
        float(spread_sample.median()) if len(spread_sample) else np.nan,
        put_volume + call_volume if put_volume + call_volume > 0.0 else np.nan,
    ]
    coverage = float(np.mean([np.isfinite(value) for value in required]))
    status = (
        "LIVE_CHAIN_READY"
        if coverage >= 0.80
        else "LIVE_CHAIN_DATA_GUARD"
    )
    summary = {
        "as_of": str(pd.to_datetime(enriched["as_of"].iloc[0]).date()),
        "spot": spot,
        "status": status,
        "coverage": coverage,
        "rate": float(rate),
        "dividend_yield": float(dividend_yield),
        "short_tenor": short,
        "long_tenor": long,
        "iv_term_slope_vol_points": (
            (long["atm_iv"] - short["atm_iv"]) * 100.0
            if np.isfinite(long["atm_iv"])
            and np.isfinite(short["atm_iv"])
            else np.nan
        ),
        "median_relative_bid_ask_spread": (
            float(spread_sample.median()) if len(spread_sample) else np.nan
        ),
        "put_call_volume_ratio": (
            put_volume / call_volume if call_volume > 0.0 else np.nan
        ),
        "put_call_open_interest_ratio": (
            put_oi / call_oi if call_oi > 0.0 else np.nan
        ),
        "contracts_with_valid_iv": int(
            enriched["implied_volatility"].notna().sum()
        ),
        "contracts_total": int(len(enriched)),
        "historical_backtest_eligible": False,
        "pricing_note": (
            "Bid/ask and open interest are observed delayed quotes; IV and "
            "delta are diagnostic Black-Scholes inversions, not vendor Greeks."
        ),
    }
    return summary, enriched
