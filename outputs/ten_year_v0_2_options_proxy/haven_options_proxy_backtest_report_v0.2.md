# 避风港全周期策略 v0.2：期权代理十年情景回测

> 回测区间：2016-07-25 至 2026-07-24  
> 交易日：2515  
> 参考 sleeve：$500,000  
> 性质：研究情景，不是历史期权链回放、实盘参数或交易建议

## 结论先行

- 把期权代理全部接入后，`QQQ_Full_35_20_45` 的 CAGR 为
  10.48%，相对纯 `P/S` 版的 10.25%
  提高 0.23个百分点；最大回撤从
  -24.85% 变为 -24.79%，
  Sharpe 从 0.71 变为
  0.73。
- **贪婪卖 Call 没有自动创造收益。** 单独加入 `G+E+R → Covered Call`
  后，CAGR 为 10.23%；完整组合十年累计收取 Call 权利金
  $18,678，但 Call 净损益为
  $-1,888，其中识别出的内在价值封顶
  成本为 $3,331。这正是必须把
  权利金与封顶成本同时记账的原因。
- Put Spread 保险的累计净投入为
  $44,825，最终净损益为
  $-6,703；在 `P≥50` 或恐慌事件有效期内
  的保险损益为 $2,707。
  保险主要改变亏损路径和危机购买力，不能用“保险本身是否盈利”单独评价。
  本样本中它把2020压力窗口的最大回撤从
  -10.69% 改善到
  -10.25%，但对2018Q4和2022几乎没有帮助。
- CSP 模块十年收取权利金
  $35,440，净损益
  $26,329，发生
  2 次模拟指派。CSP 是本情景中主要正贡献，
  但这也最依赖执行价、偏斜和真实成交质量。
- IV与点差的九组校准情景中，完整 QQQ 方案 CAGR 范围为
  10.41%～10.56%。因此报告只把结果
  当作合理区间，不把单一小数点数字当成可复制实盘收益。
- TQQQ 完整代理版 CAGR 从纯 P/S 的 21.67% 变为
  22.60%，但最大回撤仍为
  -64.73%。**TQQQ 仍不通过 Paper 门槛。**

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

| leg | entries | median_dte | median_moneyness | median_premium_to_spot | median_iv | median_abs_delta | median_contracts |
|---|---|---|---|---|---|---|---|
| cash_secured_put | 27 | 39.000 | -5.76% | 0.79% | 24.53% | 0.199 | 6.000 |
| covered_call | 35 | 42.000 | 5.66% | 0.91% | 22.10% | 0.247 | 2.000 |
| insurance_long_put | 27 | 77.000 | -6.07% | 1.87% | 24.57% | 0.249 | 5.000 |
| insurance_short_put | 27 | 77.000 | -13.69% | 0.61% | 27.15% | 0.100 | 5.000 |

这张表不是调参依据，只检查最终生成的代理合约是否落在常见的期限、虚值幅度和
Delta区间；例如基准 Call 的中位权利金约为标的现价的1%，而不是人为添加固定
月收益。

## QQQ：模块消融与完整策略

| 策略 | 累计收益 | CAGR | 波动率 | Sharpe | 最大回撤 | 最差20日 | 平均风险仓位 |
|---|---:|---:|---:|---:|---:|---:|---:|
| QQQ_PS_Only | 165.22% | 10.25% | 11.35% | 0.71 | -24.85% | -12.31% | 43.47% |
| QQQ_PS_Insurance | 163.39% | 10.17% | 11.29% | 0.71 | -24.85% | -12.31% | 43.48% |
| QQQ_PS_GreedCall | 164.78% | 10.23% | 11.34% | 0.71 | -24.85% | -12.31% | 43.48% |
| QQQ_PS_Premium | 173.29% | 10.58% | 11.52% | 0.73 | -24.77% | -12.31% | 43.57% |
| QQQ_Full_35_20_45 | 170.90% | 10.48% | 11.48% | 0.73 | -24.79% | -12.31% | 43.59% |
| QQQ_Full_50_15_35 | 232.56% | 12.77% | 13.75% | 0.78 | -27.25% | -13.88% | 56.67% |
| QQQ_Full_60_10_30 | 281.94% | 14.35% | 15.47% | 0.80 | -29.36% | -16.58% | 65.70% |
| QQQ_Static35 | 136.46% | 8.99% | 7.86% | 0.84 | -12.60% | -10.20% | 35.00% |
| QQQ_BuyHold | 546.05% | 20.52% | 22.46% | 0.84 | -35.12% | -27.85% | 100.00% |
| BIL_like_Cash | 25.98% | 2.34% | 0.12% | N/A | 0.00% | 0.00% | 0.00% |

![QQQ options ablation](qqq_options_ablation.png)

## 权利金与保险独立记账

| 策略 | Call净损益 | CSP净损益 | 保险净损益 | 接货仓损益 | 执行摩擦 | Call次数 | CSP次数/指派 | 保险次数 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| QQQ_PS_Insurance | $0 | $0 | $-6,605 | $0 | $-4,808 | 0 | 0/0 | 27 |
| QQQ_PS_GreedCall | $-2,141 | $0 | $0 | $0 | $-1,097 | 36 | 0/0 | 0 |
| QQQ_PS_Premium | $-2,141 | $29,074 | $0 | $687 | $-2,502 | 36 | 30/1 | 0 |
| QQQ_Full_35_20_45 | $-1,888 | $26,329 | $-6,703 | $1,358 | $-7,389 | 35 | 27/2 | 27 |
| TQQQ_Full_35_20_45 | $-43,120 | $255,677 | $-33,435 | $3,583 | $-90,097 | 34 | 24/2 | 26 |

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

| 策略 | 累计收益 | CAGR | 波动率 | Sharpe | 最大回撤 | 最差20日 | 平均风险仓位 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TQQQ_PS_Only | 610.31% | 21.67% | 33.65% | 0.68 | -64.78% | -33.98% | 43.47% |
| TQQQ_PS_Insurance | 597.94% | 21.46% | 33.47% | 0.68 | -64.84% | -33.98% | 43.48% |
| TQQQ_PS_GreedCall | 600.28% | 21.50% | 33.63% | 0.68 | -64.77% | -33.98% | 43.48% |
| TQQQ_Full_35_20_45 | 666.26% | 22.60% | 34.08% | 0.70 | -64.73% | -33.98% | 43.60% |
| TQQQ_Static35 | 514.18% | 19.91% | 23.26% | 0.80 | -37.66% | -28.35% | 35.00% |
| TQQQ_BuyHold | 2609.45% | 39.11% | 66.45% | 0.80 | -81.75% | -67.48% | 100.00% |
| BIL_like_Cash | 25.98% | 2.34% | 0.12% | N/A | 0.00% | 0.00% | 0.00% |

![TQQQ options ablation](tqqq_options_ablation.png)

TQQQ 只使用真实日收益路径；其目标是纳指100的单日3倍，长期结果不能以
QQQ区间收益简单乘3推导。本版仍只保留研究，不进入 Paper/Live。

## IV与点差敏感性

| scenario | iv_multiplier | spread_multiplier | cagr | sharpe_excess_bil | max_drawdown | option_net_pnl |
|---|---|---|---|---|---|---|
| IVx0.90_Spreadx0.75 | 0.900 | 0.750 | 10.48% | 0.729 | -24.87% | $18,543 |
| IVx0.90_Spreadx1.00 | 0.900 | 1.000 | 10.46% | 0.728 | -24.88% | $17,027 |
| IVx0.90_Spreadx1.50 | 0.900 | 1.500 | 10.41% | 0.725 | -24.90% | $13,397 |
| IVx1.00_Spreadx0.75 | 1.000 | 0.750 | 10.52% | 0.729 | -24.78% | $21,913 |
| IVx1.00_Spreadx1.00 | 1.000 | 1.000 | 10.48% | 0.726 | -24.79% | $19,097 |
| IVx1.00_Spreadx1.50 | 1.000 | 1.500 | 10.44% | 0.723 | -24.81% | $15,682 |
| IVx1.10_Spreadx0.75 | 1.100 | 0.750 | 10.56% | 0.732 | -24.70% | $24,892 |
| IVx1.10_Spreadx1.00 | 1.100 | 1.000 | 10.53% | 0.730 | -24.71% | $23,085 |
| IVx1.10_Spreadx1.50 | 1.100 | 1.500 | 10.49% | 0.727 | -24.73% | $19,844 |

## 100股颗粒度敏感性

| reference_nav | cagr | sharpe_excess_bil | max_drawdown | call_entries | csp_entries | insurance_entries | option_net_pnl_pct_initial |
|---|---|---|---|---|---|---|---|
| 100000.000 | 10.35% | 0.718 | -24.85% | 0.000 | 16.000 | 14.000 | 1.68% |
| 250000.000 | 10.49% | 0.726 | -24.81% | 29.000 | 24.000 | 27.000 | 4.08% |
| 500000.000 | 10.48% | 0.726 | -24.79% | 35.000 | 27.000 | 27.000 | 3.82% |
| 1000000.000 | 10.49% | 0.726 | -24.78% | 35.000 | 27.000 | 27.000 | 3.84% |

小账户不是简单按比例缩小：不足100股时，Call、Put和保险都会整张归零。
因此50万美元主情景不能直接线性外推到任意账户规模。

## 保险结构敏感性

| insurance_structure | cagr | sharpe_excess_bil | max_drawdown | insurance_net_pnl | insurance_pnl_during_panic | insurance_net_entry_debit |
|---|---|---|---|---|---|---|
| put_spread | 10.48% | 0.726 | -24.79% | $-6,703 | $2,707 | $44,825 |
| protective_put | 10.60% | 0.739 | -24.78% | $484 | $13,962 | $61,849 |

## 压力阶段

| strategy | period | start | end | return | max_drawdown | worst_day |
|---|---|---|---|---|---|---|
| QQQ_PS_Only | 2018_Q4 | 2018-10-01 | 2018-12-31 | -8.32% | -10.80% | -3.07% |
| QQQ_PS_Only | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | 3.67% | -10.69% | -4.31% |
| QQQ_PS_Only | 2022_bear | 2022-01-03 | 2022-12-30 | -22.64% | -24.66% | -4.03% |
| QQQ_PS_Insurance | 2018_Q4 | 2018-10-01 | 2018-12-31 | -8.32% | -10.80% | -3.07% |
| QQQ_PS_Insurance | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | 4.13% | -10.25% | -4.26% |
| QQQ_PS_Insurance | 2022_bear | 2022-01-03 | 2022-12-30 | -22.64% | -24.67% | -4.03% |
| QQQ_PS_GreedCall | 2018_Q4 | 2018-10-01 | 2018-12-31 | -8.31% | -10.79% | -3.07% |
| QQQ_PS_GreedCall | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | 3.67% | -10.69% | -4.31% |
| QQQ_PS_GreedCall | 2022_bear | 2022-01-03 | 2022-12-30 | -22.64% | -24.66% | -4.03% |
| QQQ_PS_Premium | 2018_Q4 | 2018-10-01 | 2018-12-31 | -8.47% | -10.96% | -3.07% |
| QQQ_PS_Premium | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | 3.67% | -10.69% | -4.31% |
| QQQ_PS_Premium | 2022_bear | 2022-01-03 | 2022-12-30 | -22.56% | -24.59% | -4.03% |
| QQQ_Full_35_20_45 | 2018_Q4 | 2018-10-01 | 2018-12-31 | -8.47% | -10.96% | -3.07% |
| QQQ_Full_35_20_45 | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | 4.13% | -10.25% | -4.26% |
| QQQ_Full_35_20_45 | 2022_bear | 2022-01-03 | 2022-12-30 | -22.58% | -24.61% | -4.03% |
| QQQ_BuyHold | 2018_Q4 | 2018-10-01 | 2018-12-31 | -16.73% | -22.69% | -4.58% |
| QQQ_BuyHold | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | -6.54% | -28.56% | -11.98% |
| QQQ_BuyHold | 2022_bear | 2022-01-03 | 2022-12-30 | -32.58% | -34.83% | -5.48% |
| TQQQ_PS_Only | 2018_Q4 | 2018-10-01 | 2018-12-31 | -25.83% | -31.31% | -8.79% |
| TQQQ_PS_Only | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | 4.88% | -30.38% | -12.41% |
| TQQQ_PS_Only | 2022_bear | 2022-01-03 | 2022-12-30 | -61.53% | -64.41% | -11.92% |
| TQQQ_Full_35_20_45 | 2018_Q4 | 2018-10-01 | 2018-12-31 | -26.76% | -32.20% | -8.79% |
| TQQQ_Full_35_20_45 | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | 6.89% | -28.92% | -12.23% |
| TQQQ_Full_35_20_45 | 2022_bear | 2022-01-03 | 2022-12-30 | -61.48% | -64.36% | -11.92% |
| TQQQ_BuyHold | 2018_Q4 | 2018-10-01 | 2018-12-31 | -47.93% | -57.53% | -13.60% |
| TQQQ_BuyHold | COVID_crash_recovery | 2020-02-19 | 2020-04-30 | -39.19% | -69.92% | -34.46% |
| TQQQ_BuyHold | 2022_bear | 2022-01-03 | 2022-12-30 | -79.20% | -81.11% | -16.46% |

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
{
  "warmup_start": "2011-07-01",
  "evaluation_start": "2016-07-25",
  "evaluation_end": "2026-07-24",
  "trading_days": 2515,
  "warmup_rows": 1273,
  "sources": {
    "market_prices": "Nasdaq historical API",
    "ndx": "FRED NASDAQ100",
    "credit": "HYG/IEF market proxy for 10-year scores; FRED BAMLH0A0HYM2 retained when available and lagged 1 day",
    "cash": "FRED DGS3MO lagged one day, less 0.1350% annual proxy expense",
    "volatility": "Cboe VXN/VIX/VIX3M histories"
  },
  "latest_dates": {
    "qqq": "2026-07-24",
    "tqqq": "2026-07-24",
    "ndx": "2026-07-27",
    "vxn": "2026-07-27",
    "credit": "2026-07-24",
    "cash_yield": "2026-07-24"
  },
  "reference_sleeve_nav": 500000.0,
  "options_proxy": {
    "historical_option_chain_used": false,
    "model": "VXN锚定Black-Scholes期权代理；不是历史期权链",
    "pricing": "Black-Scholes daily mark with VXN anchor",
    "signal_delay": "one trading day",
    "contract_multiplier": 100,
    "commission_per_contract_per_leg": 0.65,
    "base_iv_multiplier": 1.0,
    "base_spread_multiplier": 1.0,
    "main_insurance_structure": "put_spread",
    "early_assignment": "economic close/expiry proxy; exact American early assignment is not reconstructed"
  },
  "model_constraints": {
    "production_workflow_modified": false,
    "paper_or_live_enabled": false,
    "outputs_latest_modified": false,
    "tqqq_status": "stress research only"
  }
}
```
