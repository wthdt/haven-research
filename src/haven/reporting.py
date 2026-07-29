from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/haven-matplotlib")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _percent(value: float, digits: int = 2) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{value * 100:.{digits}f}%"


def _number(value: float, digits: int = 2) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{value:.{digits}f}"


def _metrics_markdown(metrics: pd.DataFrame, names: list[str]) -> str:
    selected = metrics.set_index("strategy").loc[names].reset_index()
    lines = [
        "| 策略 | 累计收益 | CAGR | 波动率 | Sharpe | 最大回撤 | 最差20日 | 平均风险仓位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in selected.iterrows():
        lines.append(
            "| {strategy} | {total} | {cagr} | {vol} | {sharpe} | "
            "{mdd} | {worst20} | {weight} |".format(
                strategy=row["strategy"],
                total=_percent(row["total_return"]),
                cagr=_percent(row["cagr"]),
                vol=_percent(row["annual_volatility"]),
                sharpe=_number(row["sharpe_excess_bil"]),
                mdd=_percent(row["max_drawdown"]),
                worst20=_percent(row["worst_20d"]),
                weight=_percent(row["average_asset_weight"]),
            )
        )
    return "\n".join(lines)


def _frame_markdown(frame: pd.DataFrame, digits: int = 4) -> str:
    if frame.empty:
        return "无可用结果。"
    columns = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] * len(columns)) + "|",
    ]
    for _, row in frame.iterrows():
        values: list[str] = []
        for column in frame.columns:
            value = row[column]
            if isinstance(value, (float, np.floating)):
                values.append("N/A" if pd.isna(value) else f"{value:.{digits}f}")
            else:
                values.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def create_equity_chart(
    results: dict[str, pd.DataFrame],
    selected: list[str],
    path: Path,
    title: str,
) -> None:
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(13, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [2.0, 1.0]},
    )
    for name in selected:
        frame = results[name]
        normalized = frame["equity"] / frame["equity"].iloc[0] * 100.0
        axes[0].plot(normalized.index, normalized, label=name, linewidth=1.6)
        axes[1].plot(
            frame.index,
            frame["drawdown"] * 100.0,
            label=name,
            linewidth=1.2,
        )
    axes[0].set_title(title)
    axes[0].set_ylabel("Growth of 100")
    axes[0].set_yscale("log")
    axes[0].grid(alpha=0.25)
    axes[0].legend(ncol=2, fontsize=9)
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].set_xlabel("Date")
    axes[1].grid(alpha=0.25)
    axes[1].axhline(0.0, color="black", linewidth=0.7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def create_signal_chart(
    scores: pd.DataFrame,
    states: pd.DataFrame,
    primary: pd.DataFrame,
    start: str,
    end: str,
    path: Path,
) -> None:
    section = scores.loc[start:end]
    state_section = states.loc[start:end]
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(13, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [1.4, 1.2, 0.8]},
    )
    axes[0].plot(section.index, section["P"], label="Panic P", linewidth=1.2)
    axes[0].plot(
        section.index, section["S"], label="Stabilization S", linewidth=1.2
    )
    axes[0].axhline(50.0, color="#b22222", linestyle="--", linewidth=0.9)
    axes[0].axhline(35.0, color="#2e8b57", linestyle=":", linewidth=0.9)
    axes[0].axhline(55.0, color="#2e8b57", linestyle="--", linewidth=0.9)
    axes[0].set_ylabel("Score")
    axes[0].set_ylim(0, 100)
    axes[0].legend(ncol=2, fontsize=9)
    axes[0].grid(alpha=0.25)

    axes[1].plot(section.index, section["G"], label="Greed G", linewidth=1.1)
    axes[1].plot(
        section.index, section["E"], label="Exhaustion E", linewidth=1.1
    )
    axes[1].plot(
        section.index,
        section["R_proxy"],
        label="Premium proxy (not P&L)",
        linewidth=0.9,
        alpha=0.8,
    )
    axes[1].axhline(60.0, color="#8b4513", linestyle="--", linewidth=0.9)
    axes[1].set_ylabel("Score")
    axes[1].set_ylim(0, 100)
    axes[1].legend(ncol=3, fontsize=9)
    axes[1].grid(alpha=0.25)

    axes[2].step(
        primary.index,
        primary["executed_asset_weight"] * 100.0,
        where="post",
        label="Executed risk-asset weight",
        linewidth=1.2,
    )
    active = state_section["event_active"].astype(float) * 100.0
    axes[2].fill_between(
        active.index,
        0.0,
        active,
        where=active.gt(0.0),
        color="#dc143c",
        alpha=0.10,
        step="post",
        label="Panic event active",
    )
    axes[2].set_ylabel("Weight (%)")
    axes[2].set_xlabel("Date")
    axes[2].set_ylim(0, 100)
    axes[2].legend(ncol=2, fontsize=9)
    axes[2].grid(alpha=0.25)
    fig.suptitle("Haven scores, panic events and deployed weight", y=1.01)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def write_report(
    output_dir: Path,
    metrics: pd.DataFrame,
    stress: pd.DataFrame,
    events: pd.DataFrame,
    sensitivity: pd.DataFrame,
    state_counts: pd.DataFrame,
    risk_translation: pd.DataFrame,
    metadata: dict[str, Any],
) -> Path:
    metric_index = metrics.set_index("strategy")
    primary_name = "QQQ_Haven_35_20_45"
    primary = metric_index.loc[primary_name]
    buy_hold = metric_index.loc["QQQ_BuyHold"]
    static_qqq = metric_index.loc["QQQ_Static35"]
    tqqq_primary = metric_index.loc["TQQQ_Haven_35_20_45"]
    tqqq_buy_hold = metric_index.loc["TQQQ_BuyHold"]
    static_tqqq = metric_index.loc["TQQQ_Static35"]
    completed_events = (
        int(events["completed"].sum()) if not events.empty else 0
    )
    event_count = int(len(events))
    best_split = (
        metrics[metrics["strategy"].str.startswith("QQQ_Haven")]
        .sort_values("sharpe_excess_bil", ascending=False)
        .iloc[0]["strategy"]
    )

    qqq_table_names = [
        "QQQ_Haven_35_20_45",
        "QQQ_Haven_50_15_35",
        "QQQ_Haven_60_10_30",
        "QQQ_Static35",
        "QQQ_BuyHold",
        "BIL_like_Cash",
    ]
    tqqq_table_names = [
        "TQQQ_Haven_35_20_45",
        "TQQQ_Haven_50_15_35",
        "TQQQ_Haven_60_10_30",
        "TQQQ_Static35",
        "TQQQ_BuyHold",
        "BIL_like_Cash",
    ]
    report = f"""# 避风港全周期策略 v0.1：十年回测报告

> 回测区间：{metadata['evaluation_start']} 至 {metadata['evaluation_end']}  
> 交易日：{metadata['trading_days']}  
> 性质：研究回测，不是实盘参数或交易建议

## 结论先行

- `QQQ_Haven_35_20_45` 十年累计收益为 {_percent(primary['total_return'])}，
  CAGR 为 {_percent(primary['cagr'])}，最大回撤为
  {_percent(primary['max_drawdown'])}。
- 与相同 35% 基础风险仓的静态方案相比，恐慌加仓把 CAGR 从
  {_percent(static_qqq['cagr'])} 提高到 {_percent(primary['cagr'])}，
  但最大回撤从 {_percent(static_qqq['max_drawdown'])} 扩大到
  {_percent(primary['max_drawdown'])}，Sharpe 从
  {_number(static_qqq['sharpe_excess_bil'])} 降至
  {_number(primary['sharpe_excess_bil'])}。**v0.1 的 P/S 资金释放没有产生
  风险调整后的优势。**
- 同期 QQQ 买入持有累计收益为 {_percent(buy_hold['total_return'])}，
  最大回撤为 {_percent(buy_hold['max_drawdown'])}。这说明策略的核心取舍是
  用现金机会成本换回撤控制，而不是保证跑赢长期牛市。
- 三组 QQQ Haven 方案中，按超额现金 Sharpe 排名最高的是
  `{best_split}`，但仍低于静态35% QQQ和 QQQ 买入持有。
- TQQQ 的无期权 sleeve 结果必须与账户级风险分开看：
  `TQQQ_Haven_35_20_45` 最大回撤为
  {_percent(tqqq_primary['max_drawdown'])}，TQQQ 买入持有最大回撤为
  {_percent(tqqq_buy_hold['max_drawdown'])}。
- TQQQ 恐慌加仓相对静态35% TQQQ 只把 CAGR 从
  {_percent(static_tqqq['cagr'])} 提高到 {_percent(tqqq_primary['cagr'])}，
  却把最大回撤从 {_percent(static_tqqq['max_drawdown'])} 扩大到
  {_percent(tqqq_primary['max_drawdown'])}。**TQQQ 版本不通过 Paper 门槛。**
- 共识别 {event_count} 轮进入样本的恐慌事件，其中 {completed_events} 轮在样本
  内完成退出。期权收益没有被理论模型伪造，因此当前结果只回答
  “评分＋状态机＋资金释放”是否有效。

### 当前研究判定

`P/S` 对 2018Q4、2020、2022 等压力阶段具有可解释性，但
`RECOVERY_RETESTED` 在 5 日不创新低并站上 MA20 后就允许释放 100% 事件预算。
在 2022 年这样的分段下跌中，短暂反弹会让风险仓从35%升到80%，随后继续
承受下跌；这是回撤扩大的主要结构性原因，而不是交易成本。

所以这一版的结论是：

1. 评分与事件日志可以保留；
2. `100%事件预算释放`需要重做，下一版先测试 40%/70%上限及动态压力约束；
3. 在资金释放优于静态基准前，不接真实 Put、Call，不进入 Paper/Live；
4. TQQQ 只保留压力研究，不作为当前可执行版本。

## QQQ 结果

{_metrics_markdown(metrics, qqq_table_names)}

![QQQ equity and drawdown](qqq_equity_drawdown.png)

## TQQQ 结果

{_metrics_markdown(metrics, tqqq_table_names)}

![TQQQ equity and drawdown](tqqq_equity_drawdown.png)

## 五分数、恐慌事件与仓位

![Scores and weights](scores_states_weights.png)

`R_proxy` 只由 VXN 与实现波动率构成，历史覆盖率被强制限制为 70%；
它不包含执行价偏斜、买卖价差、成交量、持仓量或提前指派，因此没有触发
期权交易，也没有计入收益。

## 危机阶段

{_frame_markdown(stress)}

## 恐慌事件日志

{_frame_markdown(events)}

## 状态分布

{_frame_markdown(state_counts)}

## 参数敏感性

下面只改变恐慌触发阈值和第一档企稳阈值，不从中挑选“历史最优参数”。

{_frame_markdown(sensitivity)}

## 账户风险换算

资金 `35/20/45` 等比例针对独立策略 sleeve。下表用规格中的研究压力跌幅
把 sleeve 换算到账户级上限；这不是收益回测中的强制缩放。

{_frame_markdown(risk_translation)}

## 方法与防前视

1. 恐慌、企稳等分数使用当日及此前约 5 年的滚动百分位，之后做 3 日平滑；
2. 所有当日收盘信号延迟一个交易日进入收益计算；
3. FRED 高收益债利差近年诊断列额外滞后一个交易日，不进入十年主信用代理；
4. 恐慌事件的资金释放比例是累计上限，事件内不会因评分小幅回摆机械卖出；
5. 事件退出后，v0.1 无期权版把战术 sleeve 重置到基础仓。这是为了让多轮
   恐慌可以独立比较，也是当前最重要的待验证假设之一；
6. 风险资产以外的资金使用 BIL-like 三个月国债现金代理：DGS3MO 滞后
   一日、扣除近似年费率，交易成本按单边换手 5bp 扣除；
7. 十年信用模块使用 HYG/IEF 市场代理；FRED 的 ICE BofA 利差免密下载
   目前只覆盖近约三年，因此仅保留作近年诊断，没有向前填充到早期样本；
8. ETF 价格使用 Nasdaq 历史行情，现金分红按除息日加入总收益；
9. 未计税费、融资、真实期权链、期权价差和提前指派。
10. 样本最初 82 个交易日因十年 RSP/SPY 与 HYG/IEF 代理尚未满足最短
    历史门槛而处于 `DATA_GUARD`，只保留基础仓。

## 当前阶段判断

这次回测可以验证：

- P/S 是否能在不同危机中形成可解释的事件；
- 分档释放能否改善回撤、最差 20/63 日和修复时间；
- 更高基础仓是否能缓解长期牛市中的现金拖累；
- TQQQ 路径下同一套状态机的风险是否过大。

这次回测不能验证：

- 卖 Put 的真实权利金、指派率和指派后损失；
- Protective Put、Put Spread、Collar 的真实保险效率；
- Covered Call 的真实收益与封顶机会成本。

下一阶段必须取得可靠的逐日期权链后，才允许把上述项目接入总收益。

## 数据清单

```json
{json.dumps(metadata, ensure_ascii=False, indent=2)}
```
"""
    path = output_dir / "haven_ten_year_backtest_report.md"
    path.write_text(report, encoding="utf-8")
    return path
