from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/126.0 Safari/537.36"
)


def _download_bytes(
    url: str,
    retries: int = 4,
    timeout_seconds: float = 45.0,
) -> bytes:
    attempts = max(1, int(retries))
    timeout = float(timeout_seconds)
    if timeout <= 0.0:
        raise ValueError("timeout_seconds must be positive")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/csv,*/*",
                    "Origin": "https://www.nasdaq.com",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # pragma: no cover - network path
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url}") from last_error


def _cached_bytes(
    url: str,
    cache_path: Path,
    force: bool = False,
    *,
    retries: int = 4,
    timeout_seconds: float = 45.0,
) -> bytes:
    if cache_path.exists() and not force:
        return cache_path.read_bytes()
    payload = _download_bytes(
        url,
        retries=retries,
        timeout_seconds=timeout_seconds,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(payload)
    return payload


def _number(value: Any) -> float:
    if value is None:
        return np.nan
    text = str(value).replace("$", "").replace(",", "").strip()
    if text in {"", "-", "--", "N/A", "NA", "null", "None"}:
        return np.nan
    return float(text)


def fetch_nasdaq_history(
    symbol: str,
    start: str,
    end: str,
    cache_dir: Path,
    force: bool = False,
    asset_class: str = "etf",
    request_retries: int = 4,
    request_timeout_seconds: float = 45.0,
) -> pd.DataFrame:
    params = urllib.parse.urlencode(
        {
            "assetclass": asset_class,
            "fromdate": start,
            "todate": end,
            "limit": 5000,
        }
    )
    url = f"https://api.nasdaq.com/api/quote/{symbol}/historical?{params}"
    asset_suffix = "" if asset_class == "etf" else f"_{asset_class}"
    path = (
        cache_dir
        / f"nasdaq_{symbol.lower()}{asset_suffix}_{start}_{end}.json"
    )
    payload = json.loads(
        _cached_bytes(
            url,
            path,
            force,
            retries=request_retries,
            timeout_seconds=request_timeout_seconds,
        ).decode("utf-8")
    )
    data = payload.get("data") or {}
    rows = ((data.get("tradesTable") or {}).get("rows") or [])
    if not rows:
        raise RuntimeError(f"Nasdaq 未返回 {symbol} 历史行情")

    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], format="%m/%d/%Y")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = frame[column].map(_number)
    frame = (
        frame.set_index("date")[["open", "high", "low", "close", "volume"]]
        .sort_index()
        .loc[start:end]
    )
    frame.index.name = "date"
    return frame


def fetch_nasdaq_dividends(
    symbol: str,
    cache_dir: Path,
    force: bool = False,
    request_retries: int = 4,
    request_timeout_seconds: float = 45.0,
) -> pd.Series:
    params = urllib.parse.urlencode({"assetclass": "etf", "limit": 5000})
    url = f"https://api.nasdaq.com/api/quote/{symbol}/dividends?{params}"
    path = cache_dir / f"nasdaq_{symbol.lower()}_dividends.json"
    payload = json.loads(
        _cached_bytes(
            url,
            path,
            force,
            retries=request_retries,
            timeout_seconds=request_timeout_seconds,
        ).decode("utf-8")
    )
    rows = (
        (((payload.get("data") or {}).get("dividends") or {}).get("rows"))
        or []
    )
    if not rows:
        return pd.Series(dtype=float, name="dividend")
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(
        frame["exOrEffDate"], format="%m/%d/%Y", errors="coerce"
    )
    frame["dividend"] = frame["amount"].map(_number)
    result = frame.dropna(subset=["date"]).groupby("date")["dividend"].sum()
    result.name = "dividend"
    return result.sort_index()


def add_total_return(
    history: pd.DataFrame, dividends: pd.Series | None = None
) -> pd.DataFrame:
    result = history.copy()
    if dividends is None:
        dividend = pd.Series(0.0, index=result.index)
    else:
        dividend = dividends.reindex(result.index).fillna(0.0)
    result["dividend"] = dividend
    result["total_return"] = (
        (result["close"] + result["dividend"]) / result["close"].shift(1) - 1.0
    )
    result["price_return"] = result["close"].pct_change()
    return result


def fetch_fred_series(
    series_id: str,
    cache_dir: Path,
    force: bool = False,
    request_retries: int = 4,
    request_timeout_seconds: float = 45.0,
    allow_cache_on_refresh_error: bool = False,
) -> pd.Series:
    params = urllib.parse.urlencode({"id": series_id})
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?{params}"
    path = cache_dir / f"fred_{series_id.lower()}.csv"
    refresh_error: str | None = None
    try:
        payload = _cached_bytes(
            url,
            path,
            force,
            retries=request_retries,
            timeout_seconds=request_timeout_seconds,
        )
    except RuntimeError as exc:
        if not (force and allow_cache_on_refresh_error and path.exists()):
            raise
        payload = path.read_bytes()
        refresh_error = f"{type(exc).__name__}: {exc}"
    frame = pd.read_csv(io.BytesIO(payload))
    date_column = frame.columns[0]
    value_column = frame.columns[1]
    frame[date_column] = pd.to_datetime(frame[date_column])
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    series = frame.set_index(date_column)[value_column].sort_index()
    series.name = series_id
    series.attrs["refresh_status"] = (
        "CACHE_FALLBACK" if refresh_error else ("REFRESHED" if force else "CACHE")
    )
    series.attrs["refresh_error"] = refresh_error
    return series


def supplement_ndx_from_nasdaq(
    fred_ndx: pd.Series,
    *,
    end: str,
    cache_dir: Path,
    force: bool,
    lookback_calendar_days: int = 45,
    minimum_overlap_rows: int = 3,
    maximum_overlap_difference: float = 0.02,
    request_retries: int = 4,
    request_timeout_seconds: float = 45.0,
) -> tuple[pd.Series, dict[str, Any]]:
    """Append official Nasdaq NDX closes after the latest FRED observation.

    FRED remains the historical authority. Nasdaq may only extend the tail
    after a minimum overlap window reconstructs within the configured absolute
    index-point tolerance. Existing FRED observations are never overwritten.
    """

    clean_fred = fred_ndx.dropna().sort_index()
    if clean_fred.empty:
        raise RuntimeError("FRED NASDAQ100 缓存为空，不能校验 Nasdaq NDX")

    end_date = pd.Timestamp(end).normalize()
    lookback = max(7, int(lookback_calendar_days))
    start_date = min(
        clean_fred.index.max() - pd.Timedelta(days=lookback),
        end_date - pd.Timedelta(days=lookback),
    )
    nasdaq_frame = fetch_nasdaq_history(
        "NDX",
        str(start_date.date()),
        str(end_date.date()),
        cache_dir,
        force=force,
        asset_class="index",
        request_retries=request_retries,
        request_timeout_seconds=request_timeout_seconds,
    )
    nasdaq_close = nasdaq_frame["close"].dropna().sort_index()
    overlap = pd.concat(
        [
            clean_fred.rename("fred"),
            nasdaq_close.rename("nasdaq"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    required_overlap = max(1, int(minimum_overlap_rows))
    if len(overlap) < required_overlap:
        raise RuntimeError(
            "Nasdaq NDX 与 FRED NASDAQ100 重叠样本不足："
            f"{len(overlap)} < {required_overlap}"
        )
    maximum_difference = float(
        (overlap["fred"] - overlap["nasdaq"]).abs().max()
    )
    tolerance = max(0.0, float(maximum_overlap_difference))
    if maximum_difference > tolerance:
        raise RuntimeError(
            "Nasdaq NDX 与 FRED NASDAQ100 重叠值不一致："
            f"max_abs_diff={maximum_difference:.6f} > {tolerance:.6f}"
        )

    fred_latest = clean_fred.index.max()
    tail = nasdaq_close.loc[nasdaq_close.index > fred_latest]
    fred_attrs = dict(fred_ndx.attrs)
    combined = pd.concat([fred_ndx, tail]).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    combined.name = fred_ndx.name
    combined.attrs.update(fred_attrs)
    metadata = {
        "status": "NASDAQ_TAIL_APPENDED" if not tail.empty else "NO_NEW_TAIL",
        "historical_source": "FRED NASDAQ100",
        "tail_source": "Nasdaq NDX historical API",
        "fred_latest_date": str(fred_latest.date()),
        "nasdaq_latest_date": (
            str(nasdaq_close.index.max().date())
            if not nasdaq_close.empty
            else None
        ),
        "appended_rows": int(len(tail)),
        "overlap_rows": int(len(overlap)),
        "maximum_overlap_difference": maximum_difference,
        "overlap_tolerance": tolerance,
    }
    return combined, metadata


def fetch_cboe_history(
    index_name: str,
    cache_dir: Path,
    force: bool = False,
    request_retries: int = 4,
    request_timeout_seconds: float = 45.0,
) -> pd.DataFrame:
    upper = index_name.upper()
    url = (
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/"
        f"{upper}_History.csv"
    )
    path = cache_dir / f"cboe_{upper.lower()}_history.csv"
    payload = _cached_bytes(
        url,
        path,
        force,
        retries=request_retries,
        timeout_seconds=request_timeout_seconds,
    )
    frame = pd.read_csv(io.BytesIO(payload))
    frame.columns = [str(column).strip().upper() for column in frame.columns]
    date_column = "DATE" if "DATE" in frame.columns else frame.columns[0]
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    for column in frame.columns:
        if column != date_column:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=[date_column]).set_index(date_column).sort_index()
    frame.index.name = "date"
    if "CLOSE" not in frame.columns:
        numeric_columns = list(frame.select_dtypes(include=[np.number]).columns)
        if not numeric_columns:
            raise RuntimeError(f"Cboe {upper} 文件没有数值列")
        frame = frame.rename(columns={numeric_columns[-1]: "CLOSE"})
    return frame


def build_research_dataset(
    config: dict[str, Any],
    project_root: Path,
    force: bool = False,
    *,
    request_retries: int = 4,
    request_timeout_seconds: float = 45.0,
    allow_fred_cache_on_refresh_error: bool = False,
    supplement_ndx_tail: bool = False,
    ndx_tail_lookback_calendar_days: int = 45,
    ndx_minimum_overlap_rows: int = 3,
    ndx_maximum_overlap_difference: float = 0.02,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw_dir = project_root / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    start = config["strategy"]["evaluation_start"]
    end = config["strategy"]["evaluation_end"]

    symbols = (
        config["data"]["execution_symbols"]
        + config["data"]["context_symbols"]
    )
    histories: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        history = fetch_nasdaq_history(
            symbol,
            start,
            end,
            raw_dir,
            force,
            request_retries=request_retries,
            request_timeout_seconds=request_timeout_seconds,
        )
        dividends = fetch_nasdaq_dividends(
            symbol,
            raw_dir,
            force,
            request_retries=request_retries,
            request_timeout_seconds=request_timeout_seconds,
        )
        histories[symbol] = add_total_return(history, dividends)

    fred_config = config["data"]["fred"]
    ndx = fetch_fred_series(
        fred_config["ndx"],
        raw_dir,
        force,
        request_retries=request_retries,
        request_timeout_seconds=request_timeout_seconds,
        allow_cache_on_refresh_error=allow_fred_cache_on_refresh_error,
    )
    ndx_supplement_metadata: dict[str, Any] | None = None
    if supplement_ndx_tail:
        ndx, ndx_supplement_metadata = supplement_ndx_from_nasdaq(
            ndx,
            end=end,
            cache_dir=raw_dir,
            force=force,
            lookback_calendar_days=ndx_tail_lookback_calendar_days,
            minimum_overlap_rows=ndx_minimum_overlap_rows,
            maximum_overlap_difference=ndx_maximum_overlap_difference,
            request_retries=request_retries,
            request_timeout_seconds=request_timeout_seconds,
        )
    high_yield = fetch_fred_series(
        fred_config["high_yield_spread"],
        raw_dir,
        force,
        request_retries=request_retries,
        request_timeout_seconds=request_timeout_seconds,
        allow_cache_on_refresh_error=allow_fred_cache_on_refresh_error,
    )
    cash_yield = fetch_fred_series(
        fred_config["cash_yield"],
        raw_dir,
        force,
        request_retries=request_retries,
        request_timeout_seconds=request_timeout_seconds,
        allow_cache_on_refresh_error=allow_fred_cache_on_refresh_error,
    )

    cboe_config = config["data"]["cboe"]
    vxn = fetch_cboe_history(
        cboe_config["vxn"],
        raw_dir,
        force,
        request_retries=request_retries,
        request_timeout_seconds=request_timeout_seconds,
    )["CLOSE"]
    vix = fetch_cboe_history(
        cboe_config["vix"],
        raw_dir,
        force,
        request_retries=request_retries,
        request_timeout_seconds=request_timeout_seconds,
    )["CLOSE"]
    vix3m = fetch_cboe_history(
        cboe_config["vix3m"],
        raw_dir,
        force,
        request_retries=request_retries,
        request_timeout_seconds=request_timeout_seconds,
    )["CLOSE"]

    warmup_start = config["strategy"]["warmup_start"]
    # 评分使用纳斯达克 100 的更长历史作为 QQQ 环境代理；交易收益仍只用
    # Nasdaq 返回的真实 QQQ/TQQQ/BIL 行情。
    master_index = ndx.loc[warmup_start:end].dropna().index
    dataset = pd.DataFrame(index=master_index)
    for symbol, history in histories.items():
        lower = symbol.lower()
        for column in history.columns:
            dataset[f"{lower}_{column}"] = history[column].reindex(master_index)

    dataset["ndx_close"] = ndx.reindex(master_index).ffill(limit=3)
    dataset["vxn_close"] = vxn.reindex(master_index).ffill(limit=3)
    dataset["vix_close"] = vix.reindex(master_index).ffill(limit=3)
    dataset["vix3m_close"] = vix3m.reindex(master_index).ffill(limit=3)
    lag = int(config["backtest"]["credit_data_lag_days"])
    dataset["hy_spread"] = (
        high_yield.reindex(master_index).ffill(limit=7).shift(lag)
    )
    # ICE BofA 期权调整高收益债利差通过 FRED 的免密图表接口目前只返回
    # 近约 3 年。十年回测使用 HYG/IEF 相对价格作为信用风险代理，避免把
    # 后来的利差数据向前填入历史。真实利差仍保留为近年诊断列。
    dataset["credit_proxy"] = (
        dataset["hyg_close"] / dataset["ief_close"]
    )
    cash_yield_aligned = cash_yield.reindex(master_index).ffill(limit=7).shift(1)
    cash_expense = float(config["backtest"]["cash_proxy_expense_ratio"])
    dataset["cash_yield_pct"] = cash_yield_aligned
    dataset["cash_total_return"] = (
        (cash_yield_aligned / 100.0 - cash_expense) / 252.0
    ).clip(lower=0.0)

    dataset = dataset.loc[warmup_start:end].copy()
    dataset.index.name = "date"
    evaluation = dataset.loc[start:end]
    metadata = {
        "warmup_start": str(dataset.index.min().date()),
        "evaluation_start": str(evaluation.index.min().date()),
        "evaluation_end": str(evaluation.index.max().date()),
        "trading_days": int(len(evaluation)),
        "warmup_rows": int(len(dataset) - len(evaluation)),
        "sources": {
            "market_prices": "Nasdaq historical API",
            "ndx": (
                f"FRED {fred_config['ndx']}"
                + (
                    " plus overlap-validated Nasdaq NDX tail"
                    if ndx_supplement_metadata is not None
                    else ""
                )
            ),
            "credit": (
                "HYG/IEF market proxy for 10-year scores; "
                f"FRED {fred_config['high_yield_spread']} retained when available "
                f"and lagged {lag} day"
            ),
            "cash": (
                f"FRED {fred_config['cash_yield']} lagged one day, less "
                f"{cash_expense:.4%} annual proxy expense"
            ),
            "volatility": "Cboe VXN/VIX/VIX3M histories",
        },
        "latest_dates": {
            "qqq": str(histories["QQQ"].index.max().date()),
            "tqqq": str(histories["TQQQ"].index.max().date()),
            "ndx": str(ndx.dropna().index.max().date()),
            "vxn": str(vxn.dropna().index.max().date()),
            "credit": str(high_yield.dropna().index.max().date()),
            "cash_yield": str(cash_yield.dropna().index.max().date()),
        },
        "refresh_status": {
            "fred": {
                fred_config["ndx"]: ndx.attrs.get(
                    "refresh_status",
                    "UNKNOWN",
                ),
                fred_config["high_yield_spread"]: high_yield.attrs.get(
                    "refresh_status",
                    "UNKNOWN",
                ),
                fred_config["cash_yield"]: cash_yield.attrs.get(
                    "refresh_status",
                    "UNKNOWN",
                ),
            },
            "ndx_tail": ndx_supplement_metadata,
        },
    }
    return dataset, metadata
