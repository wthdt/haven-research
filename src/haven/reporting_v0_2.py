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


def _money(value: float, digits: int = 0) -> str:
    if pd.isna(value):
        return "N/A"
    return f"${value:,.{digits}f}"


def _number(value: float, digits: int = 2) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{value:.{digits}f}"


def _metrics_markdown(metrics: pd.DataFrame, names: list[str]) -> str:
    available = [name for name in names if name in set(metrics["strategy"])]
    selected = metrics.set_index("strategy").loc[available].reset_index()
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


def _option_summary_markdown(
    option_summary: pd.DataFrame,
    names: list[str],
) -> str:
    available = [
        name for name in names if name in set(option_summary["strategy"])
    ]
    selected = option_summary.set_index("strategy").loc[available].reset_index()
    lines = [
        "| 策略 | Call净损益 | CSP净损益 | 保险净损益 | 接货仓损益 | 执行摩擦 | Call次数 | CSP次数/指派 | 保险次数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in selected.iterrows():
        lines.append(
            "| {strategy} | {call} | {csp} | {insurance} | {assigned} | "
            "{drag} | {call_n} | {csp_n}/{assigned_n} | {insurance_n} |".format(
                strategy=row["strategy"],
                call=_money(row["covered_call_net_pnl"]),
                csp=_money(row["cash_secured_put_net_pnl"]),
                insurance=_money(row["insurance_net_pnl"]),
                assigned=_money(row["assigned_inventory_net_pnl"]),
                drag=_money(-row["execution_drag"]),
                call_n=int(row["covered_call_entry_groups"]),
                csp_n=int(row["csp_entry_groups"]),
                assigned_n=int(row["csp_assignments"]),
                insurance_n=int(row["insurance_entry_groups"]),
            )
        )
    return "\n".join(lines)


def _frame_markdown(
    frame: pd.DataFrame,
    percent_columns: set[str] | None = None,
    money_columns: set[str] | None = None,
    digits: int = 3,
) -> str:
    if frame.empty:
        return "无可用结果。"
    percent_columns = percent_columns or set()
    money_columns = money_columns or set()
    lines = [
        "| " + " | ".join(map(str, frame.columns)) + " |",
        "|" + "|".join(["---"] * len(frame.columns)) + "|",
    ]
    for _, row in frame.iterrows():
        values: list[str] = []
        for column in frame.columns:
            value = row[column]
            if column in percent_columns and pd.notna(value):
                values.append(_percent(float(value)))
            elif column in money_columns and pd.notna(value):
                values.append(_money(float(value)))
            elif isinstance(value, (float, np.floating)):
                values.append(
                    "N/A" if pd.isna(value) else f"{float(value):.{digits}f}"
                )
            else:
                values.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def create_equity_drawdown_chart(
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
        axes[0].plot(normalized.index, normalized, label=name, linewidth=1.5)
        axes[1].plot(
            frame.index,
            frame["drawdown"] * 100.0,
            label=name,
            linewidth=1.1,
        )
    axes[0].set_title(title)
    axes[0].set_ylabel("Growth of 100")
    axes[0].set_yscale("log")
    axes[0].grid(alpha=0.25)
    axes[0].legend(ncol=2, fontsize=8)
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].set_xlabel("Date")
    axes[1].grid(alpha=0.25)
    axes[1].axhline(0.0, color="black", linewidth=0.7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def create_option_attribution_chart(
    frame: pd.DataFrame,
    initial_nav: float,
    path: Path,
    title: str,
) -> None:
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(13, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [1.7, 1.0]},
    )
    series = [
        ("covered_call_pnl", "Covered Call"),
        ("cash_secured_put_pnl", "Cash-secured Put"),
        ("insurance_pnl", "Put-spread insurance"),
        ("assigned_inventory_pnl", "Assigned inventory"),
    ]
    for column, label in series:
        cumulative = frame[column].cumsum() / initial_nav * 100.0
        axes[0].plot(cumulative.index, cumulative, label=label, linewidth=1.5)
    axes[0].axhline(0.0, color="black", linewidth=0.7)
    axes[0].set_title(title)
    axes[0].set_ylabel("Cumulative P&L (% initial NAV)")
    axes[0].grid(alpha=0.25)
    axes[0].legend(ncol=2, fontsize=9)

    axes[1].plot(
        frame.index,
        frame["covered_call_coverage"] * 100.0,
        label="Call coverage",
        linewidth=1.1,
    )
    axes[1].plot(
        frame.index,
        frame["cash_secured_put_collateral_weight"] * 100.0,
        label="CSP collateral",
        linewidth=1.1,
    )
    axes[1].plot(
        frame.index,
        frame["assigned_weight"] * 100.0,
        label="Assigned inventory",
        linewidth=1.1,
    )
    insurance_active = frame["insurance_contracts"].gt(0).astype(float) * 5.0
    axes[1].fill_between(
        frame.index,
        0.0,
        insurance_active,
        where=insurance_active.gt(0.0),
        alpha=0.18,
        step="post",
        label="Insurance active",
    )
    axes[1].set_ylabel("Exposure / collateral (%)")
    axes[1].set_xlabel("Date")
    axes[1].grid(alpha=0.25)
    axes[1].legend(ncol=4, fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def create_greed_call_chart(
    frame: pd.DataFrame,
    path: Path,
) -> None:
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(13, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [1.5, 0.8]},
    )
    axes[0].plot(frame.index, frame["G"], label="Greed G", linewidth=1.1)
    axes[0].plot(
        frame.index, frame["E"], label="Exhaustion E", linewidth=1.1
    )
    axes[0].plot(
        frame.index,
        frame["R_proxy"],
        label="Premium proxy R",
        linewidth=0.9,
        alpha=0.85,
    )
    axes[0].axhline(60.0, color="#8b4513", linestyle="--", linewidth=0.8)
    axes[0].set_ylim(0, 100)
    axes[0].set_ylabel("Score")
    axes[0].set_title("Greed, exhaustion and covered-call activation")
    axes[0].legend(ncol=3, fontsize=9)
    axes[0].grid(alpha=0.25)

    coverage = frame["covered_call_coverage"] * 100.0
    axes[1].fill_between(
        coverage.index,
        0.0,
        coverage,
        where=coverage.gt(0.0),
        color="#6a5acd",
        alpha=0.35,
        step="post",
        label="Covered-call share coverage",
    )
    axes[1].set_ylabel("Coverage (%)")
    axes[1].set_xlabel("Date")
    axes[1].grid(alpha=0.25)
    axes[1].legend(fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def write_options_proxy_report(
    output_dir: Path,
    metrics: pd.DataFrame,
    option_summary: pd.DataFrame,
    trade_diagnostics: pd.DataFrame,
    stress: pd.DataFrame,
    sensitivity: pd.DataFrame,
    nav_sensitivity: pd.DataFrame,
    structure_sensitivity: pd.DataFrame,
    metadata: dict[str, Any],
) -> Path:
    metric = metrics.set_index("strategy")
    base = metric.loc["QQQ_PS_Only"]
    insurance = metric.loc["QQQ_PS_Insurance"]
    call = metric.loc["QQQ_PS_GreedCall"]
    premium = metric.loc["QQQ_PS_Premium"]
    full = metric.loc["QQQ_Full_35_20_45"]
    tqqq_base = metric.loc["TQQQ_PS_Only"]
    tqqq_full = metric.loc["TQQQ_Full_35_20_45"]
    full_summary = option_summary.set_index("strategy").loc[
        "QQQ_Full_35_20_45"
    ]
    sensitivity_min = sensitivity["cagr"].min()
    sensitivity_max = sensitivity["cagr"].max()
    stress_index = stress.set_index(["strategy", "period"])
    covid_base_drawdown = float(
        stress_index.loc[
            ("QQQ_PS_Only", "COVID_crash_recovery"), "max_drawdown"
        ]
    )
    covid_full_drawdown = float(
        stress_index.loc[
            ("QQQ_Full_35_20_45", "COVID_crash_recovery"),
            "max_drawdown",
        ]
    )

    qqq_names = [
        "QQQ_PS_Only",
        "QQQ_PS_Insurance",
        "QQQ_PS_GreedCall",
        "QQQ_PS_Premium",
        "QQQ_Full_35_20_45",
        "QQQ_Full_50_15_35",
        "QQQ_Full_60_10_30",
        "QQQ_Static35",
        "QQQ_BuyHold",
        "BIL_like_Cash",
    ]
    tqqq_names = [
        "TQQQ_PS_Only",
        "TQQQ_PS_Insurance",
        "TQQQ_PS_GreedCall",
        "TQQQ_Full_35_20_45",
        "TQQQ_Static35",
        "TQQQ_BuyHold",
        "BIL_like_Cash",
    ]
    option_names = [
        "QQQ_PS_Insurance",
        "QQQ_PS_GreedCall",
        "QQQ_PS_Premium",
        "QQQ_Full_35_20_45",
        "TQQQ_Full_35_20_45",
    ]

    sensitivity_display = sensitivity[
        [
            "scenario",
            "iv_multiplier",
            "spread_multiplier",
            "cagr",
            "sharpe_excess_bil",
            "max_drawdown",
            "option_net_pnl",
        ]
    ].copy()
    nav_display = nav_sensitivity[
        [
            "reference_nav",
            "cagr",
            "sharpe_excess_bil",
            "max_drawdown",
            "call_entries",
            "csp_entries",
            "insurance_entries",
            "option_net_pnl_pct_initial",
        ]
    ].copy()
    structure_display = structure_sensitivity[
        [
            "insurance_structure",
            "cagr",
            "sharpe_excess_bil",
            "max_drawdown",
            "insurance_net_pnl",
            "insurance_pnl_during_panic",
            "insurance_net_entry_debit",
        ]
    ].copy()
    diagnostics_display = trade_diagnostics[
        [
            "leg",
            "entries",
            "median_dte",
            "median_moneyness",
            "median_premium_to_spot",
            "median_iv",
            "median_abs_delta",
            "median_contracts",
        ]
    ].copy()

    report = f"""# 避风港全周期策略 v0.2：期权代理十年情景回测

> 回测区间：{metadata['evaluation_start']} 至 {metadata['evaluation_end']}  
> 交易日：{metadata['trading_days']}  
> 参考 sleeve：{_money(metadata['reference_sleeve_nav'])}  
> 性质：研究情景，不是历史期权链回放、实盘参数或交易建议

## 结论先行

- 把期权代理全部接入后，`QQQ_Full_35_20_45` 的 CAGR 为
  {_percent(full['cagr'])}，相对纯 `P/S` 版的 {_percent(base['cagr'])}
  提高 {(full['cagr'] - base['cagr']) * 100:.2f}个百分点；最大回撤从
  {_percent(base['max_drawdown'])} 变为 {_percent(full['max_drawdown'])}，
  Sharpe 从 {_number(base['sharpe_excess_bil'])} 变为
  {_number(full['sharpe_excess_bil'])}。
- **贪婪卖 Call 没有自动创造收益。** 单独加入 `G+E+R → Covered Call`
  后，CAGR 为 {_percent(call['cagr'])}；完整组合十年累计收取 Call 权利金
  {_money(full_summary['call_premium_collected'])}，但 Call 净损益为
  {_money(full_summary['covered_call_net_pnl'])}，其中识别出的内在价值封顶
  成本为 {_money(full_summary['call_intrinsic_cap_cost'])}。这正是必须把
  权利金与封顶成本同时记账的原因。
- Put Spread 保险的累计净投入为
  {_money(full_summary['insurance_net_entry_debit'])}，最终净损益为
  {_money(full_summary['insurance_net_pnl'])}；在 `P≥50` 或恐慌事件有效期内
  的保险损益为 {_money(full_summary['insurance_pnl_during_panic'])}。
  保险主要改变亏损路径和危机购买力，不能用“保险本身是否盈利”单独评价。
  本样本中它把2020压力窗口的最大回撤从
  {_percent(covid_base_drawdown)} 改善到
  {_percent(covid_full_drawdown)}，但对2018Q4和2022几乎没有帮助。
- CSP 模块十年收取权利金
  {_money(full_summary['csp_premium_collected'])}，净损益
  {_money(full_summary['cash_secured_put_net_pnl'])}，发生
  {int(full_summary['csp_assignments'])} 次模拟指派。CSP 是本情景中主要正贡献，
  但这也最依赖执行价、偏斜和真实成交质量。
- IV与点差的九组校准情景中，完整 QQQ 方案 CAGR 范围为
  {_percent(sensitivity_min)}～{_percent(sensitivity_max)}。因此报告只把结果
  当作合理区间，不把单一小数点数字当成可复制实盘收益。
- TQQQ 完整代理版 CAGR 从纯 P/S 的 {_percent(tqqq_base['cagr'])} 变为
  {_percent(tqqq_full['cagr'])}，但最大回撤仍为
  {_percent(tqqq_full['max_drawdown'])}。**TQQQ 仍不通过 Paper 门槛。**

## 这次“模拟”了什么

1. 用每日 VXN 作为 QQQ 约30天隐含波动率锚；
2. TQQQ 的 IV 使用其自身20日实现波动率相对 QQQ 的滚动比例放大，而不是
   简单固定乘3；
3. 用 Black-Scholes 生成每日理论中间价，并加入期限结构、Put skew、
   Call wing、买卖价差、滑点和每腿每张 `$0.65` 费用；
4. Call 目标期限约35天、Delta 0.20～0.25；保险主结构为约75天的
   `-0.25/+0.10 Delta` Put Spread；CSP 约35天、Delta 0.15～0.20；
5. 全部使用100股整数合约；信号延迟一个交易日；月度到期日按第三个周五附近
   的真实交易日模拟；
6. CSP 全额现金担保，并保留25%自由现金底线；ITM到期会生成接货仓；
7. 保险按 v0.1 规则分批兑现；Call 仅在
   `RANGE_CARRY/GREED_TREND/GREED_EXHAUSTING` 的联合门槛满足时开启。

没有模拟的部分包括真实逐日执行价链、真实 bid/ask、成交量/持仓量、精确美式
提前行权、税务与券商保证金。因此它是**路径一致的代理情景**，不是历史成交复刻。

### 开仓参数的事后合理性检查

{_frame_markdown(
    diagnostics_display,
    percent_columns={
        'median_moneyness',
        'median_premium_to_spot',
        'median_iv',
    },
)}

这张表不是调参依据，只检查最终生成的代理合约是否落在常见的期限、虚值幅度和
Delta区间；例如基准 Call 的中位权利金约为标的现价的1%，而不是人为添加固定
月收益。

## QQQ：模块消融与完整策略

{_metrics_markdown(metrics, qqq_names)}

![QQQ options ablation](qqq_options_ablation.png)

## 权利金与保险独立记账

{_option_summary_markdown(option_summary, option_names)}

`权利金收入`是开仓时收到的毛现金，不等于利润。净损益已经扣除回补、到期
内在价值、价差、滑点和费用。

![Option PnL attribution](option_pnl_attribution.png)

## 贪婪指数卖 Call

本版延续 v0.1 的门槛：

- `RANGE_CARRY`：`G≥50、E≥40、R≥55`，最多覆盖25%；
- `GREED_TREND`：只有 `G≥75、R≥70` 才允许最多覆盖10%；
- `GREED_EXHAUSTING`：基础覆盖25%，仅在
  `G≥75、E≥60、R≥60` 时提高到50%；
- 恐慌事件、风险预警和恢复期全部禁止新卖 Call；事件结束后锁定10个交易日。

![Greed call activation](greed_call_activation.png)

## TQQQ 压力研究

{_metrics_markdown(metrics, tqqq_names)}

![TQQQ options ablation](tqqq_options_ablation.png)

TQQQ 只使用真实日收益路径；其目标是纳指100的单日3倍，长期结果不能以
QQQ区间收益简单乘3推导。本版仍只保留研究，不进入 Paper/Live。

## IV与点差敏感性

{_frame_markdown(
    sensitivity_display,
    percent_columns={'cagr', 'max_drawdown'},
    money_columns={'option_net_pnl'},
)}

## 100股颗粒度敏感性

{_frame_markdown(
    nav_display,
    percent_columns={'cagr', 'max_drawdown', 'option_net_pnl_pct_initial'},
)}

小账户不是简单按比例缩小：不足100股时，Call、Put和保险都会整张归零。
因此50万美元主情景不能直接线性外推到任意账户规模。

## 保险结构敏感性

{_frame_markdown(
    structure_display,
    percent_columns={'cagr', 'max_drawdown'},
    money_columns={
        'insurance_net_pnl',
        'insurance_pnl_during_panic',
        'insurance_net_entry_debit',
    },
)}

## 压力阶段

{_frame_markdown(
    stress,
    percent_columns={'return', 'max_drawdown', 'worst_day'},
)}

## 防前视与执行规则

1. P/S/G/E/R 全部只使用当日及历史数据；
2. 期权信号整体延迟一个交易日，随后用执行日收盘附近的代理 bid/ask 建仓；
3. 期权持仓每日重新估值，IV会随VXN和已实现波动率变化；
4. 期权毛权利金、净损益、保险损益、接货仓收益与执行摩擦分别记账；
5. 参数在读取最终净值前固定；敏感性结果全部保留，没有只挑最好情景；
6. 基础 P/S 状态机保持 v0.1 不变，因此
   `RECOVERY_RETESTED` 过早释放100%事件预算的问题仍然存在；
7. 不修改生产策略、Shadow、Paper/Live、券商订单或 `outputs/latest`。

## 口径依据

- [Cboe 波动率指数方法](https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Selected_Broad_Based_Index_Equity_and_ETF_Volatility_Indices.pdf)
- [OIC Covered Call](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write)
- [OIC Cash-Secured Put](https://www.optionseducation.org/strategies/all-strategies/cash-secured-put)
- [OIC Protective Put](https://www.optionseducation.org/strategies/all-strategies/protective-put-married-put)
- [ProShares TQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq)

## 数据与运行清单

```json
{json.dumps(metadata, ensure_ascii=False, indent=2)}
```
"""
    path = output_dir / "haven_options_proxy_backtest_report_v0.2.md"
    path.write_text(report, encoding="utf-8")
    return path
