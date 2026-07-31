# 安全交易策略项目迁移规格书

> 文档用途：将“避风港 / Haven 安全交易模型”交给另一个开发环境，从零重新实现并复验。  
> 规格基准日期：2026-07-29。  
> 代码基准：`CODE_COMMIT 62d00e2`。  
> 证据基准：`EVIDENCE_COMMIT 799624537aaf21a8a89fba309bb4498b792e5989`。  
> Git 仓库：`https://github.com/wthdt/haven-research.git`。  
> 分支：`codex-review/haven-v04-hermes`。  
> 当前性质：研究、回测和 Shadow；不是 Paper、Live 或实盘策略。  
> 本文中的“待确认”表示现有对话、配置和代码没有确定值，迁移时不得自行猜测。

---

## 1. 文档证据优先级

迁移实现发生冲突时，按以下顺序裁决：

1. `CODE_COMMIT 62d00e2` 的可执行代码与 YAML 配置；
2. `EVIDENCE_COMMIT 7996245` 的回测输出、测试输出和验收材料；
3. 项目 Library 中的 v0.1、v0.2、v0.3、v0.4 策略文档和回测报告；
4. 已发生的 Codex 审核对话；
5. 讨论性文字。

不得把旧报告、旧 Smoke JSON 或 Hermes 的交付自述覆盖到当前代码事实上。

### 1.1 当前独立复验结果

- 最新远端分支 HEAD：`799624537aaf21a8a89fba309bb4498b792e5989`。
- 当前代码提交：`62d00e2`。
- 单元与集成测试：原始迁移包独立复跑 `37/37` 通过；迁移后的网络超时与 NDX 尾部校验优化为 `44/44` 通过。
- 六个正式入口：独立复跑全部退出码为 `0`。
- v0.3 基线：
  - CAGR：`0.1044407284611435`
  - 最大回撤：`-0.24286070662036596`
  - Sharpe（超额 BIL）：`0.7518621910781986`
- v0.4 基线：
  - CAGR：`0.11436087961156871`
  - 最大回撤：`-0.16465738482313375`
  - Sharpe（超额 BIL）：`0.9491753193458002`
- `R_put / R_call / R` Audit 内部重建误差：
  - `R_put = 0`
  - `R_call = 0`
  - `R = 7.11e-15`
- Haven Cron：未创建、未启用。

### 1.2 项目边界

本规格只描述 Haven 安全交易模型。以下项目与用户有关，但不是本项目组成：

- `ai_investment_platform_v1` 五 Agent 股票研究平台；
- ETF Regime Rotation Detector；
- Triple Strategy Shadow；
- Paper Broker；
- 港股资金面周更和微盘创新药数据库。

迁移时不得把上述系统的 Agent、配置、Cron、组合或数据结构并入 Haven。

---

## 2. 状态定义

| 状态 | 定义 |
|---|---|
| 已实现 | 当前代码存在，且可运行、可测试或有固定输出证据 |
| 已确定但未实现 | 规则或约束已经明确，但当前代码没有完整执行路径 |
| 讨论中/不确定 | 曾提出，但没有定稿参数、实现或验收 |
| 已放弃/已作废 | 已明确不再作为当前方案，或因错误被废止 |
| 已替代 | 旧版本曾实现，但已被新版本正式替换 |

---

## 3. 项目目标和非目标

### 3.1 项目目标

项目目标是建立一个确定性、无前视、研究优先的纳斯达克风险管理模型：

1. 始终保留基础参与仓，降低长期完全空仓风险；
2. 用 `P` 识别恐慌和价格错位；
3. 用 `S` 判断下跌是否减速并控制分批买入；
4. 用 `G` 识别过热和拥挤；
5. 用 `E` 识别上涨衰竭；
6. 用 `R_put / R_call` 判断卖 Put 和卖 Call 的权利金是否合算；
7. 用 `I` 判断保险需求与保险价格；
8. 用 `N` 观察新闻和事件，但不得改变仓位；
9. 用资金三层、事件累计释放、期权现金占用和保险限制尾部风险；
10. 所有信号在收盘计算，于下一交易日生效；
11. 默认先做回测与 Shadow，确认可靠后才讨论 Paper 或真实交易。

策略目标是账户长期风险调整后收益，不是：

- 猜中最低点；
- 权利金收入最大化；
- 长期跑赢 QQQ；
- 用 Covered Call 代替止损或保险；
- 用 TQQQ 提供无约束杠杆。

### 3.2 非目标

以下均不是当前项目目标：

- 自动下单；
- 连接券商；
- 创建、修改或撤销真实订单；
- Paper 或 Live 交易；
- 使用当前成分股重建十年前真实宽度；
- 用当前期权链回填历史；
- 用主观新闻补全 `N`；
- 精确重建税务、保证金或美式期权提前行权；
- 建设网页、移动端页面或交易 Dashboard；
- 将语言模型用于计算交易分数。

---

## 4. 版本演进与状态矩阵

| 项目 | 当前状态 | 说明 |
|---|---|---|
| v0.1 `P/S/G/E/R_proxy` | 已实现 | 五评分、事件状态机、三层资金、QQQ/TQQQ 无期权回测 |
| v0.2 期权代理 | 已实现 | Black-Scholes 代理、CSP、Covered Call、Put Spread、逐日记账 |
| v0.3 `I` 保险模型 | 已实现 | 90 日 Protective Put、保险预算、分批兑现和风险记忆 |
| v0.4 指标增强 | 已实现 | QQEW 宽度代理、信用/流动性、VVIX/SKEW/期限结构 |
| `R_put / R_call / R` 分解 | 已实现 | 共享引擎中间值，Audit 可精确重建 |
| `N` 新闻模型 | 已实现为 Shadow | 无 X 授权时为 `SHADOW_NO_DATA`，永不影响仓位 |
| 实时纳指 100 宽度 | 已实现为 Shadow | 当前成分股快照，不用于历史回测 |
| QQQ/TQQQ 延迟期权链 | 已实现为 Shadow | 观察 Bid/Ask、IV、Delta、成交量、持仓量 |
| Hermes 原生插件 | 已实现 | 4 个工具、1 个 Skill、`/haven` 命令 |
| Hermes 收盘消息 | 已实现但未调度 | 两阶段账本和投递状态绑定已实现 |
| Haven Cron | 已确定但未启用 | 必须显式 `--create-cron`；当前不存在 |
| `DATA_GUARD` | 已实现 | 引擎与 Hermes 层均有数据保护 |
| `HARD_RISK_BREACH` | 已确定但未实现 | 文档有优先级，当前状态机没有账户输入或生成路径 |
| 账户级 15%/6%/4% 风险否决 | 已确定但未实现 | 当前仅生成风险换算报告，不否决仓位 |
| 动态回撤熔断 | 未实现 | 没有固定净值回撤阈值和自动清仓逻辑 |
| Paper / Live | 未实现且禁止 | 当前所有配置均为 `research_only` |
| 券商订单 | 未实现且禁止 | 不连接券商，不生成订单 |
| 真实历史期权链回放 | 未实现 | 当前为 VXN 锚定代理 |
| Walk-forward 参数训练 | 已确定但未实现 | 文档要求走步检验；当前基线使用固定参数 |
| Web 页面 / Dashboard | 未实现 | 当前只有 CLI、Hermes 工具、CSV、JSON 和 PNG |
| Covered Call 自动执行 | 已放弃当前启用 | 十年净 P&L 为负，禁止自动执行 |
| TQQQ Paper / Live | 已放弃当前启用 | 仅压力研究 |
| QEW 历史宽度代理 | 已作废 | 错误数据仅 89 日，曾导致 81.8% 日期进入 DATA_GUARD |
| QQEW 历史宽度代理 | 已实现 | 取代 QEW，历史 2,512 行 |
| v0.2 75 日 Put Spread 默认保险 | 已替代 | v0.3 改为 90 日 Protective Put |
| 早期“一企稳即退出保险” | 已放弃 | 改为 20 日风险记忆、75 日最短持有和残余保护 |

---

## 5. 支持的市场、品种、周期和数据源

### 5.1 市场与品种

| 类别 | 品种 | 角色 | 状态 |
|---|---|---|---|
| 美国 ETF | QQQ | 主要执行标的和期权代理标的 | 已实现 |
| 美国杠杆 ETF | TQQQ | 压力研究、期权筛选 | 已实现为研究；禁止 Paper/Live |
| 现金代理 | BIL-like | 非风险资产收益 | 已实现 |
| 宽度代理 | RSP/SPY | v0.1 宽度和集中度 | 已实现 |
| 纳指宽度代理 | QQEW/QQQ | v0.4 十年因果宽度 | 已实现 |
| 信用代理 | HYG/IEF | 十年信用压力代理 | 已实现 |
| 指数 | Nasdaq-100 | 环境识别 | 已实现 |
| 波动率指数 | VXN/VIX/VIX3M/VVIX/SKEW/VIX9D/VIX6M | 波动率、尾部、期限结构 | 已实现 |
| 期权 | QQQ/TQQQ Call、Put | 代理回测和延迟链 Shadow | 已实现为研究 |

不支持 A 股、港股、期货、外汇、加密资产或个股实盘。

### 5.2 时间周期

- 决策周期：日频；
- 计算时间：美国市场收盘后；
- 生效时间：下一交易日；
- 评分滚动窗口：`1260` 个交易日；
- 一般指标最小历史：`252` 个交易日；
- 短历史指标最小历史：`63` 个交易日；
- 最终评分平滑：`3` 日简单平均；
- 回测 warm-up：`2011-07-01` 起；
- 正式评估：`2016-07-25` 至 `2026-07-24`；
- v0.1 数据行：`2515` 个交易日；
- 期权完整回测因非交易行过滤后：`2514` 个交易日；
- 年化交易日：`252`。

### 5.3 数据源与因果滞后

| 数据 | 来源 | 滞后 | 前向填充上限 |
|---|---|---:|---:|
| QQQ/TQQQ/RSP/SPY/QQEW/HYG/IEF/BIL OHLCV | Nasdaq Historical API | 0 | 不跨缺失交易日造价 |
| ETF 分红 | Nasdaq Dividends API | 除息日计入 | 无 |
| Nasdaq-100 | FRED `NASDAQ100` 历史序列；实时尾部为重叠校验后的 Nasdaq NDX 官方历史收盘 | 0 | 3 日 |
| 高收益债 OAS | FRED `BAMLH0A0HYM2` | 1 交易日 | 7 日 |
| CCC OAS | FRED `BAMLH0A3HYC` | 1 交易日 | 7 日 |
| NFCI | FRED `NFCI` | 3 交易日 | 10 日 |
| 2 年美债 | FRED `DGS2` | 1 交易日 | 7 日 |
| 10 年实际利率 | FRED `DFII10` | 1 交易日 | 7 日 |
| 3 月美债现金利率 | FRED `DGS3MO` | 1 交易日 | 7 日 |
| VXN/VIX/VIX3M | Cboe 官方历史 CSV | 0 | 3 日 |
| VVIX/SKEW/VIX9D/VIX6M | Cboe 官方历史 CSV | 0 | 3 日 |
| 当前纳指 100 成分股 | Nasdaq 当前成员及成员历史 | 当前快照 | 不可回填历史 |
| 当前 QQQ/TQQQ 期权链 | Nasdaq 延迟链 | 延迟行情 | 不可回填历史 |
| X 帖子计数 | X API | 最近 7 天小时计数 | 无授权时为空 |

NFCI 使用最新修订历史，并非严格 vintage 数据；必须保留修订偏差警告。

实时 Shadow 的 NDX 尾部补全必须同时满足：

1. FRED `NASDAQ100` 非空且继续保留为历史权威序列；
2. Nasdaq NDX 与 FRED 至少有 3 个非空重叠交易日；
3. 重叠日收盘价最大绝对差不超过 0.02 指数点；
4. 只追加 FRED 最新非空日期之后的 Nasdaq 行，不改写 FRED 历史；
5. 任一校验失败立即进入 `DATA_GUARD`。

强制刷新时允许由配置显式启用 FRED 现有缓存回退。快照必须记录每条
FRED 序列的刷新状态以及 NDX 尾部的来源、重叠行数、最大差异和追加行数；
缓存不存在时不得伪造数据。

---

## 6. 通用计算规则

### 6.1 滚动百分位

对序列 \(x_t\)，使用截至 \(t\) 的最近 `1260` 个值：

\[
Percentile_t =
100\times
\frac{N(x<x_t)+0.5N(x=x_t)}{N(valid)}
\]

结果截断到 `[1, 99]`。不得使用未来数据。

### 6.2 压力映射

\[
Stress(p)=clip(2(p-50),0,100)
\]

中位百分位映射为 `0` 压力，而不是 `50`。

### 6.3 缺失值权重重归一化

基础复合分：

\[
RawScore_t=
\frac{\sum_i x_{i,t}w_i\mathbf 1(x_{i,t}\ne NA)}
{\sum_i w_i\mathbf 1(x_{i,t}\ne NA)}
\]

\[
Coverage_t=\sum_i w_i\mathbf 1(x_{i,t}\ne NA)
\]

基础组件名义权重之和均为 `1.0`，`R_proxy` 例外：组件权重之和为 `0.70`，并强制覆盖率上限 `0.70`。

### 6.4 三日平滑

\[
Score_t=clip\left(\frac{RawScore_t+RawScore_{t-1}+RawScore_{t-2}}{n_t},0,100\right)
\]

其中 \(n_t\) 为窗口中实际存在的天数，最少 `1`。

### 6.5 v0.4 覆盖率感知混合

对 Overlay 或 `R` 分解：

\[
a_{i,t}=w_i\times coverage_{i,t}\times \mathbf 1(score_{i,t}\ne NA)
\]

\[
w^{eff}_{i,t}=\frac{a_{i,t}}{\sum_j a_{j,t}}
\]

\[
Blend_t=\sum_i score_{i,t}\times w^{eff}_{i,t}
\]

\[
BlendCoverage_t=\frac{\sum_i a_{i,t}}{\sum_i w_i}
\]

Audit 的 contribution 必须使用未舍入值：

\[
contribution_{i,t}=score_{i,t}\times w^{eff}_{i,t}
\]

---

## 7. 基础模型的精确公式

下表中的“Pct”表示第 6.1 节滚动百分位；“StressPct”表示先取百分位再执行第 6.2 节压力映射。

### 7.1 `P` 恐慌模型

| 组件 | 原始量 | 变换方向 | 权重 |
|---|---|---|---:|
| `drawdown_63` | \(1-close/\max_{63}(close)\) | StressPct | 0.12 |
| `drawdown_252` | \(1-close/\max_{252}(close)\) | StressPct | 0.10 |
| `below_ma200` | \(clip(1-close/MA200,0,\infty)\) | StressPct | 0.08 |
| `negative_return_5` | \(-Return_5\) | StressPct | 0.05 |
| `vxn_level` | VXN | StressPct | 0.15 |
| `vxn_change_5` | VXN 5 日涨幅 | StressPct | 0.10 |
| `breadth_rel_20` | \(-Return_{20}(RSP/SPY)\) | StressPct，短历史 | 0.10 |
| `breadth_rel_63` | \(-Return_{63}(RSP/SPY)\) | StressPct，短历史 | 0.10 |
| `credit_stress_level` | \(1-(HYG/IEF)/\max_{252}(HYG/IEF)\) | StressPct，短历史 | 0.05 |
| `credit_widening` | \(-Return_{20}(HYG/IEF)\) | StressPct，短历史 | 0.05 |
| `vix_term_stress` | \(VIX/VIX3M\) | StressPct | 0.10 |

价格错位子分数 `P_price` 只使用前四项，并按其可用权重重归一化。

### 7.2 `S` 企稳模型

| 组件 | 原始量 | 变换 | 权重 |
|---|---|---|---:|
| `return_5` | 5 日收益 | Pct | 0.10 |
| `return_10` | 10 日收益 | Pct | 0.10 |
| `above_ma10` | `close/MA10 - 1` | Pct | 0.075 |
| `above_ma20` | `close/MA20 - 1` | Pct | 0.075 |
| `vxn_cooling` | `-VXN 5日涨幅` | Pct | 0.10 |
| `vxn_off_high` | `1 - VXN/max20(VXN)` | Pct | 0.10 |
| `breadth_rel_5` | 5 日 `RSP/SPY` 相对收益 | Pct，短历史 | 0.125 |
| `breadth_rel_20` | 20 日 `RSP/SPY` 相对收益 | Pct，短历史 | 0.125 |
| `credit_narrowing` | 20 日 `HYG/IEF` 收益 | Pct，短历史 | 0.10 |
| `no_new_low_5` | 今日 close 高于此前 5 日最低值时为 100，否则 0 | 直接值 | 0.10 |

### 7.3 `G` 贪婪模型

| 组件 | 原始量 | 变换 | 权重 |
|---|---|---|---:|
| `return_20` | 20 日收益 | Pct | 0.10 |
| `return_63` | 63 日收益 | Pct | 0.10 |
| `above_ma20` | `close/MA20 - 1` | Pct | 0.10 |
| `low_vxn` | `-VXN` | Pct | 0.15 |
| `term_contango` | `-(VIX/VIX3M)` | Pct | 0.10 |
| `above_ma200` | `close/MA200 - 1` | Pct | 0.10 |
| `return_252` | 252 日收益 | Pct | 0.10 |
| `breadth_concentration_20` | `-Return20(RSP/SPY)` | Pct，短历史 | 0.075 |
| `breadth_concentration_63` | `-Return63(RSP/SPY)` | Pct，短历史 | 0.075 |
| `rsi` | RSI(14) | Pct | 0.05 |
| `up_day_share` | 最近 10 日上涨日占比 | Pct | 0.05 |

RSI 使用 Wilder 风格 `EWMA(alpha=1/14, adjust=False)`。

### 7.4 `E` 衰竭模型

| 组件 | 原始量 | 变换 | 权重 |
|---|---|---|---:|
| `momentum_deceleration` | `Return20 - 4 × Return5` | Pct | 0.20 |
| `macd_rollover` | `-Δ5(MACD12,26)` | Pct | 0.15 |
| `breadth_weakening_5` | `-Return5(RSP/SPY)` | Pct，短历史 | 0.125 |
| `breadth_weakening_20` | `-Return20(RSP/SPY)` | Pct，短历史 | 0.125 |
| `failed_breakout` | `1-close/max20(close)` | Pct | 0.20 |
| `volume_price_divergence` | `max(-QQQ日收益,0) × volume/MA20(volume)` | Pct，短历史 | 0.10 |
| `relative_strength_weakening` | `-Return20(QQQ/SPY)` | Pct，短历史 | 0.05 |
| `rsi_rollover` | `max10(RSI14)-RSI14` | Pct | 0.05 |

### 7.5 `R_proxy`

\[
RV20=Std(Return_1,20)\sqrt{252}\times100
\]

\[
IVMinusRV=VXN-RV20
\]

| 组件 | 变换 | 权重 |
|---|---|---:|
| VXN | Pct | 0.35 |
| `VXN - RV20` | Pct | 0.35 |

历史执行价、偏斜、成交量、持仓量和真实价差缺失，因此覆盖率强制不高于 `0.70`，`option_backtest_allowed=false`。

### 7.6 `I` 保险模型

\[
RiskLevel=clip\left(\frac{P-20}{40}\times100,0,100\right)
\]

\[
RiskAcceleration=clip\left(\frac{\max(P-P_{t-5},0)}{20}\times100,0,100\right)
\]

\[
Fragility=clip(0.60E+0.40G,0,100)
\]

\[
I_{need}=clip(0.50RiskLevel+0.30RiskAcceleration+0.20Fragility,0,100)
\]

v0.4 使用：

\[
I_{affordability}=clip(100-R_{put},0,100)
\]

\[
I=clip(0.75I_{need}+0.25I_{affordability},0,100)
\]

---

## 8. v0.4 增强指标公式

### 8.1 QQEW 宽度卫星

令：

\[
Rel=QQEW/QQQ
\]

#### `breadth_stress`

- `StressPct(-Return5(Rel))`，权重 `0.20`
- `StressPct(-Return20(Rel))`，权重 `0.50`
- `StressPct(-Return63(Rel))`，权重 `0.30`

#### `breadth_recovery`

- `Pct(Return5(Rel))`，权重 `0.55`
- `Pct(Return20(Rel))`，权重 `0.45`

#### `breadth_concentration`

- `Pct(-Return20(Rel))`，权重 `0.60`
- `Pct(-Return63(Rel))`，权重 `0.40`

#### `breadth_exhaustion`

- `Pct(-Return5(Rel))`，权重 `0.60`
- `Pct(-Return20(Rel))`，权重 `0.40`

所有宽度卫星均做 3 日平滑。

### 8.2 流动性卫星

#### `liquidity_stress`

| 组件 | 公式 | 权重 |
|---|---|---:|
| HY OAS 水平 | `StressPct(HY)` | 0.12 |
| HY 20 日走阔 | `StressPct(HY.diff(20))` | 0.12 |
| CCC OAS 水平 | `StressPct(CCC)` | 0.14 |
| CCC 20 日走阔 | `StressPct(CCC.diff(20))` | 0.14 |
| NFCI 水平 | `StressPct(NFCI)` | 0.18 |
| NFCI 4 周收紧 | `StressPct(NFCI.diff(20))` | 0.10 |
| 2 年利率 5 日冲击 | `StressPct(DGS2.diff(5))` | 0.10 |
| 10 年实际利率 20 日冲击 | `StressPct(DFII10.diff(20))` | 0.10 |

#### `liquidity_relief`

| 组件 | 公式 | 权重 |
|---|---|---:|
| HY 收窄 | `Pct(-HY.diff(20))` | 0.18 |
| CCC 收窄 | `Pct(-CCC.diff(20))` | 0.18 |
| NFCI 宽松水平 | `Pct(-NFCI)` | 0.22 |
| NFCI 4 周宽松 | `Pct(-NFCI.diff(20))` | 0.18 |
| 2 年利率缓解 | `Pct(-DGS2.diff(5))` | 0.12 |
| 实际利率缓解 | `Pct(-DFII10.diff(20))` | 0.12 |

### 8.3 尾部压力

\[
ShortTermRatio=VIX9D/VIX3M
\]

\[
SixMonthRatio=VIX/VIX6M
\]

| 组件 | 公式 | 权重 |
|---|---|---:|
| VVIX 水平 | `StressPct(VVIX)` | 0.40 |
| VVIX 5 日变化 | `StressPct(VVIX.pct_change(5))` | 0.20 |
| 短期限倒挂 | `StressPct(VIX9D/VIX3M)` | 0.25 |
| 六月期限倒挂 | `StressPct(VIX/VIX6M)` | 0.15 |

\[
tail\_complacency=100-tail\_stress
\]

### 8.4 `R_put / R_call / R`

\[
SkewRichness=Pct(SKEW)
\]

\[
VVIXRichness=Pct(VVIX)
\]

\[
TermRichness=Pct(VIX9D/VIX3M)
\]

使用第 6.5 节覆盖率感知权重：

\[
R_{put}=Blend(
0.55R_{proxy},
0.25SkewRichness,
0.10VVIXRichness,
0.10TermRichness)
\]

\[
R_{call}=Blend(
0.65R_{proxy},
0.15(100-SkewRichness),
0.10VVIXRichness,
0.10TermRichness)
\]

\[
R=Blend(0.50R_{put},0.50R_{call})
\]

### 8.5 P/S/G/E Overlay

\[
P=Blend(0.70P_{base},0.15BreadthStress,0.10LiquidityStress,0.05TailStress)
\]

\[
S=Blend(0.75S_{base},0.15BreadthRecovery,0.10LiquidityRelief)
\]

\[
G=Blend(0.85G_{base},0.10BreadthConcentration,0.05TailComplacency)
\]

\[
E=Blend(0.75E_{base},0.20BreadthExhaustion,0.05TailStress)
\]

### 8.6 `N` Shadow

名义权重：

- `event_severity`：`0.35`
- `x_heat`：`0.20`
- `negative_sentiment`：`0.20`
- `source_consensus`：`0.15`
- `event_proximity`：`0.10`

\[
negative\_sentiment=
\max(-x\_sentiment,0)\times clip(x\_credibility,0,100)/100
\]

按可用权重重归一化。状态：

- 无有效分数：`SHADOW_NO_DATA / UNKNOWN`
- `N ≥ 60`：`SHADOW_ALERT / RISK_OFF`
- `40 ≤ N < 60`：`SHADOW_WATCH / WATCH`
- `N < 40`：`SHADOW_NORMAL / NEUTRAL`

固定约束：

```text
N_affects_position = false
```

---

## 9. 状态机

### 9.1 决策优先级

已确定优先级：

1. `DATA_GUARD`
2. `HARD_RISK_BREACH`
3. 已启动的恐慌事件及恢复
4. 风险预警
5. 贪婪、衰竭、权利金
6. 区间或普通参与

当前代码实际执行第 1、3、4、5、6 项；第 2 项未实现。

### 9.2 恐慌事件启动

启动条件：

- `P ≥ 50` 连续 `2` 个交易日；或
- `P ≥ 70` 单日；
- 同时 `P_price ≥ 40`。

只有 `P_coverage ≥ 0.80` 且 `P_price` 有效时才允许启动。

### 9.3 恐慌事件退出

- `P < 30` 连续 `10` 个交易日；
- 退出后事件部署比例重置为 `0`；
- 目标仓位回到基础参与仓；
- 已部署比例在事件内只增不减；
- 状态升级可单日发生；
- 状态降级需连续 `2` 日确认；
- 重新创新低时立即按候选状态重判。

### 9.4 状态条件与累计事件预算

| 状态 | 条件 | 事件预算累计释放上限 |
|---|---|---:|
| `PANIC_ACCELERATING` | 事件中，`S < 35` | 0% |
| 极端试仓 | 上述状态且 `P ≥ 80` | 事件预算 5%，并且新增风险仓不超过 NAV 1% |
| `PANIC_STABILIZING_1` | `35 ≤ S < 55` | 15% |
| `PANIC_STABILIZING_2` | `55 ≤ S < 70` | 40% |
| `RECOVERY_EARLY` | `S ≥ 70` 且通过 Early Gate | 70% |
| `RECOVERY_RETESTED` | `S ≥ 70`、价格高于 MA20、距事件低点至少 5 日、通过 Retested Gate | 100% |

v0.4 Gate：

| Gate | 宽度恢复 | 流动性改善 | 尾部压力 | 距低点 |
|---|---:|---:|---:|---:|
| Early | `≥55` | `≥30` | `≤85` | 0 |
| Retested | `≥60` | `≥40` | `≤75` | `≥10` 交易日 |

Retested 同时受基础 `5` 日不创新低要求约束，因此实际最小值为 `10` 日。

### 9.5 非事件状态

| 状态 | 条件 |
|---|---|
| `RISK_WARNING` | `30 ≤ P < 50` |
| `GREED_EXHAUSTING` | `G ≥ 60` 且 `E ≥ 40` |
| `GREED_TREND` | `G ≥ 60` 且 `E < 40` |
| `RANGE_CARRY` | 区间条件成立 |
| `NORMAL_PARTICIPATION` | 其余有效状态 |
| `DATA_GUARD` | 核心评分无效或覆盖不足 |

`RANGE_CARRY`：

- `abs(QQQ 20 日收益) ≤ 5%`
- `ADX(14) < 20`，或最近 20 日穿越 MA20 至少 `4` 次
- QQQ 20 日实现波动率百分位 `≤70`
- 不存在恐慌事件

---

## 10. 仓位管理和风险控制

### 10.1 三层资金

| 配置 | 基础仓 | Put 接货仓 | 恐慌储备 |
|---|---:|---:|---:|
| `35/20/45` | 35% | 20% | 45% |
| `50/15/35` | 50% | 15% | 35% |
| `60/10/30` | 60% | 10% | 30% |

v0.3 和 v0.4 正式基线使用 `35/20/45`。

无期权风险资产目标仓位：

\[
TargetWeight=Baseline+PanicReserve\times DeploymentFraction
\]

极端试仓状态额外仓位上限为 NAV `1%`。  
信号仓位延迟一个交易日生效。

### 10.2 事件预算

文档已确定：

\[
B_{event}=\min(
FreeCash,
SingleAssetRemaining,
RiskFactorRemaining,
StressLossBudget/MarginalStressDrop)
\]

\[
B_{new}=\max(0,B_{event}\times ReleaseFraction-Deployed)
\]

当前回测代码没有逐笔实现上述四项账户约束，只使用策略 sleeve 的目标权重和期权容量。此公式属于“已确定但未完整实现”。

### 10.3 风险研究默认值

| 参数 | 数值 | 执行状态 |
|---|---:|---|
| 账户标准压力损失上限 | NAV 15% | 只在文档；未执行否决 |
| 普通风险因子压力损失上限 | NAV 6% | 只做 sleeve 换算 |
| 杠杆 ETF 压力损失上限 | NAV 4% | 只做 sleeve 换算 |
| 最低自由现金 | NAV 25% | 期权容量执行；基础仓回测未硬性执行 |
| QQQ 压力跌幅 | 35% | 风险换算 |
| TQQQ 压力跌幅 | 65% | 风险换算 |

风险换算：

\[
MaxAccountShare=
\min\left(1,\frac{RiskLossLimit}{SleeveAssetWeight\times StressDrop}\right)
\]

### 10.4 已知风险控制缺口

1. `HARD_RISK_BREACH` 没有代码生成路径；
2. 账户级压力上限不参与每日仓位否决；
3. 没有净值回撤熔断阈值；
4. 没有核心仓价格止损；
5. `35/20/45` 完全部署后风险资产可达 80%，只剩 20% 现金，与 25% 研究自由现金线存在冲突；
6. 最低自由现金主要用于限制 CSP、指派仓和期权容量；
7. 真实账户已有仓位、相关性和跨策略风险未进入引擎。

---

## 11. 入场、出场、止损和止盈

### 11.1 核心 ETF 直接仓位

入场：

- 基础仓按所选资金分层维持；
- 恐慌事件按 0/15/40/70/100% 累计释放；
- 极端试仓仅在 `P ≥ 80`。

出场：

- 事件结束后目标仓位回到基础仓；
- 非事件的 `RISK_WARNING/GREED` 不机械清空基础仓。

止损：

- 没有固定百分比止损；
- 没有已实现的最大回撤熔断；
- 风险控制依赖仓位上限、事件状态和保险。

止盈：

- 核心仓没有价格止盈；
- 事件退出导致战术仓回落至基础仓，属于状态退出。

### 11.2 Cash-Secured Put

共同参数：

- 目标 DTE：`35`
- 允许 DTE：`28–50`
- 目标 Delta：区间 `0.20`，恐慌企稳 `0.15`
- 止盈：获得初始权利金的 `75%`
- 冷却：`5` 个交易日
- 每张合约：`100` 股
- 同时最多一个 CSP group
- 保险持仓存在时不新开 CSP
- `E > 55` 禁止新开 CSP

状态规则：

| 状态 | `R_put` | Put 资金桶比例 | 最低净折价 |
|---|---:|---:|---:|
| `RANGE_CARRY` | `≥55` | 100% | 5% |
| `PANIC_STABILIZING_1` | `≥60` | 25% | 8% |
| `PANIC_STABILIZING_2` | `≥60` | 50% | 10% |

净接货约束：

\[
K-Bid \le Spot\times(1-MinDiscount)
\]

容量：

\[
BucketCapacity=
\max(0,NAV\times PutBucket\times BucketFraction-AssignedValue)
\]

\[
FreeCashCapacity=
\max(0,NAV\times(1-PlannedWeight-0.25)
-AssignedValue-ExistingCollateral)
\]

\[
Contracts=
\left\lfloor
\frac{\min(BucketCapacity,FreeCashCapacity)}{100K}
\right\rfloor
\]

到期：

- Put ITM：按 `100 × contracts` 生成指派仓；
- 指派成本：`strike - 每股初始净权利金`；
- Put OTM：按内在价值 `0` 结算；
- 模型没有提前指派。

### 11.3 指派仓

退出任一条件：

1. `G ≥ 60`、`E ≥ 40`，且价格至少高于成本 `3%`；
2. 持有满 `252` 个交易日，且价格不低于成本；
3. 指派仓使 `planned_weight + assigned_value` 违反 25% 自由现金容量。

卖出成本按标的交易成本 `5bp`。

### 11.4 Covered Call

共同参数：

- 目标 DTE：`35`
- 允许 DTE：`28–50`
- DTE `≤5` 平仓/移仓
- Delta `≥0.80` 且 DTE `≤10` 平仓/移仓
- 止盈：初始权利金的 `75%`
- 冷却：`3` 个交易日
- 恐慌退出后锁定：`10` 个交易日
- 仅允许整数合约，不得裸卖
- 保险持仓存在时不新开 Call

| 状态 | 联合门槛 | 最大覆盖 | Delta | 最低上涨缓冲 |
|---|---|---:|---:|---:|
| `RANGE_CARRY` | `G≥50,E≥40,R_call≥55` | 25% | 0.20 | 5% |
| `GREED_TREND` | `G≥75,R_call≥70` | 10% | 0.15 | 8% |
| `GREED_EXHAUSTING` 普通 | `R_call≥55` | 25% | 0.25 | 4% |
| `GREED_EXHAUSTING` 高度过热 | `G≥75,E≥60,R_call≥60` | 50% | 0.25 | 4% |

\[
Contracts=
\left\lfloor\frac{TotalShares\times Coverage}{100}\right\rfloor
\]

有效卖出价：

\[
EffectiveExit=K+Bid-Commission/100
\]

平仓条件：

- 状态门槛不再满足；
- 进入恐慌事件；
- 进入 `RISK_WARNING`；
- DTE、Delta 或止盈条件触发。

当前研究判定：Covered Call 十年净 P&L 为负，不得自动执行。

### 11.5 保险

v0.3/v0.4 默认：

- 结构：Protective Put
- 目标 DTE：`90`
- 允许 DTE：`72–115`
- 长 Put 目标 Delta：`-0.20`
- 最大覆盖：风险资产 100%
- 单次净保费上限：NAV `0.30%`
- 年度总净保费硬上限：NAV `1.00%`
- 十年实际平均记录值：NAV `0.62%/年`
- 最短持有：`75` 日历日
- DTE `≤10` 退出/移仓
- 冷却：`5` 个交易日
- 风险记忆：`20` 个交易日
- 动态覆盖：关闭

允许入场状态：

- `RISK_WARNING`
- `GREED_EXHAUSTING`
- `NORMAL_PARTICIPATION`
- `RANGE_CARRY`

门槛：

- `I_need ≥ 55`
- `I ≥ 48`
- `I_affordability ≥ 10`
- `R_put ≤ 90`
- 保险 Alert 在触发后保持 `20` 个交易日

合约数：

\[
TargetContracts=
\left\lfloor\frac{TotalShares\times1.00}{100}\right\rfloor
\]

\[
PerEntryBudget=NAV\times0.003
\]

\[
RemainingAnnualBudget=NAV\times0.01-YearDebit
\]

\[
Contracts=\min\left(
TargetContracts,
\left\lfloor\frac{\min(PerEntryBudget,RemainingAnnualBudget)}{UnitDebit}\right\rfloor
\right)
\]

兑现：

- `P ≥ 70` 或保险利润达到初始保费 `100%`：累计平 `20%`
- `P ≥ 85` 或进入 `PANIC_STABILIZING_1`：累计平 `50%`
- 风险仍有效时至少保留初始合约 `50%`，且不少于 `1` 张

退出：

- 事件已结束；
- 持有至少 `75` 日历日；
- 最近 `15` 个交易日的 `P` 最大值 `<15`；
- 或 DTE `≤10`。

配置中的 `recovery_s_min=101` 使普通 `S` 恢复退出不可达；当前退出实际依赖“风险消失”或临近到期。

---

## 12. 期权代理定价和执行假设

### 12.1 Black-Scholes

当前只使用欧洲式 Black-Scholes 作为代理：

\[
d_1=
\frac{\ln(S/K)+(r-q+\sigma^2/2)T}{\sigma\sqrt T}
\]

\[
d_2=d_1-\sigma\sqrt T
\]

\[
C=Se^{-qT}N(d_1)-Ke^{-rT}N(d_2)
\]

\[
P=Ke^{-rT}N(-d_2)-Se^{-qT}N(-d_1)
\]

Call Delta：

\[
\Delta_C=e^{-qT}N(d_1)
\]

Put Delta：

\[
\Delta_P=e^{-qT}(N(d_1)-1)
\]

### 12.2 IV 代理

QQQ：

\[
ATMIV=\max(VXN/100,RV20_{QQQ}\times0.95)
\]

截断到 `[0.08, 1.50]`。

TQQQ：

\[
Ratio_t=
median_{63}\left(\frac{RV20_{TQQQ}}{RV20_{QQQ}}\right)
\]

最少 `20` 个值，截断到 `[1.80,3.30]`。

\[
ATMIV_{TQQQ}=\max(VXN/100\times Ratio,RV20_{TQQQ}\times0.95)
\]

截断到 `[0.20,3.00]`。

超过 30 日期限：

\[
TermIV=
\sqrt{\frac{ATMIV^2\times30+LongRunIV^2\times(DTE-30)}{DTE}}
\]

`LongRunIV` 为过去 `252` 日 ATM IV 中位数，最少 `63` 日。

偏斜：

- Put 每 10% OTM，IV 增加 `15%`
- Call 每 10% OTM，IV 减少 `3%`

### 12.3 到期日和行权价

- 月度到期日：当月第三个周五之前或当天的最后一个实际交易日；
- 在 DTE 允许区间中选取最接近目标 DTE 的到期日；
- 标的价格 `<25` 时行权价步长 `0.5`；
- 标的价格 `≥25` 时步长 `1.0`；
- 在候选行权价中最小化 `abs(abs(delta)-target_delta)`。

### 12.4 点差、佣金和成交价

| 参数 | QQQ | TQQQ |
|---|---:|---:|
| 总点差占理论中间价 | 5% | 7% |
| 每股最小总点差 | 0.02 美元 | 0.02 美元 |
| 每腿每张开/平佣金 | 0.65 美元 | 0.65 美元 |
| 最低可交易期权价格 | 0.05 美元 | 0.05 美元 |

\[
FullSpread=\max(0.02,Mid\times SpreadFraction)
\]

\[
Bid=\max(0,Mid-FullSpread/2)
\]

\[
Ask=Mid+FullSpread/2
\]

- 买入按 Ask；
- 卖出按 Bid；
- 日终持仓按 Mid 标记；
- 普通平仓：多头按 Bid，空头按 Ask；
- 到期按内在价值结算，不收结算佣金；
- 标的调仓成本：单边换手 `5bp`；
- 税费：未模拟；
- 保证金：未模拟；
- 提前行权：未模拟；
- 融资利息：未模拟；
- 闲置现金：按 DGS3MO 减 `0.135%` 年费率的 BIL-like 日收益。

### 12.5 信号和成交时点

- 当日收盘生成状态；
- 状态先 `shift(1)`；
- 次日使用该状态执行；
- 标的回测用次日整日总收益；
- 期权在次日代理行情的 Bid/Ask 成交；
- 不存在真实 next-open、VWAP 或逐笔成交价格；
- 真实成交时间假设：待确认。

---

## 13. 回测方法

### 13.1 无期权回测

\[
GrossReturn_t=
Weight_t\times AssetReturn_t+
(1-Weight_t)\times CashReturn_t
\]

\[
Turnover_t=|Weight_t-Weight_{t-1}|
\]

\[
TransactionCost_t=Turnover_t\times0.0005
\]

\[
PortfolioReturn_t=GrossReturn_t-TransactionCost_t
\]

\[
Equity_t=InitialNAV\prod_{\tau\le t}(1+PortfolioReturn_\tau)
\]

v0.1 初始 NAV：`100,000` 美元。  
v0.2/v0.3/v0.4 期权回测初始 NAV：`500,000` 美元。

### 13.2 期权回测

逐日顺序：

1. 计算基础仓和现金收益；
2. 计算指派仓相对现金的增量收益；
3. 按 Mid 标记已有期权；
4. 处理到期、指派和结算；
5. 读取前一交易日状态；
6. 管理 Covered Call 平仓；
7. 管理 CSP 止盈；
8. 管理保险兑现和退出；
9. 管理指派仓退出；
10. 尝试新开保险；
11. 尝试新开 CSP；
12. 尝试新开 Covered Call；
13. 写入独立 P&L 和净值。

同一类型同时只保留一个 group。保险存在时不新开 CSP 或 Covered Call。

### 13.3 评价指标

\[
CAGR=(Ending/Beginning)^{1/Years}-1
\]

\[
AnnualVol=Std(DailyReturn)\sqrt{252}
\]

\[
Sharpe=
\frac{Mean(Return-CashReturn)}{Std(Return-CashReturn)}
\sqrt{252}
\]

\[
Sortino=
\frac{Mean(Excess)\times252}
{\sqrt{Mean(\min(Excess,0)^2)}\sqrt{252}}
\]

\[
Drawdown_t=Equity_t/\max_{\tau\le t}Equity_\tau-1
\]

CVaR：

- `alpha = 0.05`
- 先取日收益 5% 分位；
- 再对不高于该分位的日收益求均值。

输出还包括：

- 总收益；
- 最差 1/5/20/63 日收益；
- 最大回撤；
- 回撤峰值、谷值、修复日期和修复交易日；
- 平均风险资产与现金仓位；
- 年换手；
- 交易成本；
- 期权模块独立 P&L；
- 2018Q4、2020、2022 压力期。

### 13.4 已有基准结果

#### v0.1

| 策略 | CAGR | 最大回撤 | Sharpe |
|---|---:|---:|---:|
| QQQ Haven 35/20/45 | 10.2497% | -24.8477% | 0.7143 |
| QQQ Static 35% | 8.9910% | -12.5982% | 0.8422 |
| QQQ Buy & Hold | 20.5202% | -35.1225% | 0.8422 |
| TQQQ Haven 35/20/45 | 当前重跑 21.6690% | -64.7778% | 0.6838 |
| TQQQ Buy & Hold | 当前重跑 39.1064% | -81.7545% | 0.7992 |

TQQQ 数字会因 Nasdaq 最新历史调整数据变化而与早期报告不同；TQQQ 不作为验收主基线。

#### v0.2

| 策略 | CAGR | 最大回撤 | Sharpe |
|---|---:|---:|---:|
| QQQ 完整 35/20/45 | 10.4838% | -24.7937% | 0.7263 |

早期 QQQ 模块独立结果：

- Covered Call 净 P&L：`-$1,887.6909221966896`
- CSP 净 P&L：`+$26,329.180155239737`（2026-07-29 当前数据重跑）
- Put Spread 保险净 P&L：`-$6,703.234639479616`
- 指派仓净 P&L：`+$1,358.3629440475972`

#### v0.3

| 策略 | CAGR | 最大回撤 | Sharpe |
|---|---:|---:|---:|
| P/S，无期权 | 10.2491% | -24.8477% | 0.7145 |
| I 保险 | 10.2137% | -24.3045% | 0.7389 |
| 完整策略 | 10.4441% | -24.2861% | 0.7519 |

v0.3 完整保险净 P&L：`-$9,698.459069264316`。

#### v0.4

| 策略 | CAGR | 最大回撤 | Sharpe | 平均风险资产 |
|---|---:|---:|---:|---:|
| 无期权增强状态机 | 11.2804% | -17.0077% | 0.8867 | 40.77% |
| 完整策略 | 11.4361% | -16.4657% | 0.9492 | 40.77% |

v0.4 模块消融：

| 模块 | CAGR | 最大回撤 | Sharpe |
|---|---:|---:|---:|
| 不含期权 | 11.28% | -17.01% | 0.887 |
| 只卖 Call | 11.27% | -17.01% | 0.886 |
| 只卖 Put | 11.54% | -17.01% | 0.877 |
| 只买保险 | 11.24% | -16.46% | 0.933 |
| 全部期权 | 11.44% | -16.47% | 0.949 |

v0.4 期权模块：

- Covered Call：`-$1,829.0874`
- CSP：`+$18,322.3572`
- 保险：`-$10,528.1597`
- 指派仓：`0`
- 执行拖累：`$4,319.5739`

---

## 14. 用户操作流程

### 14.1 本地研究

```bash
git clone https://github.com/wthdt/haven-research.git
cd haven-research
git checkout 799624537aaf21a8a89fba309bb4498b792e5989
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
```

运行：

```bash
python3 scripts/run_ten_year.py
python3 scripts/run_options_proxy_v0_2.py
python3 scripts/run_insurance_model_v0_3.py
python3 scripts/run_insurance_recommended_v0_3.py
python3 scripts/run_enriched_indicators_v0_4.py
python3 scripts/run_live_shadow_v0_4.py
python3 -m unittest discover -s tests -v
```

### 14.2 每日 Shadow

1. 收盘后运行 `run_live_shadow_v0_4.py`；
2. 更新正式价格、FRED、Cboe 数据；若 FRED 的 NDX 尾部落后，则按
   5.3 节的重叠校验规则追加 Nasdaq 官方 NDX 收盘；
3. 计算 P/S/G/E/R/I；
4. 生成当前真实宽度；
5. 生成延迟 QQQ 链摘要；
6. 读取 X 授权；无授权则输出 `SHADOW_NO_DATA`；
7. 写入 `current_snapshot.json`；
8. 追加 `live_shadow_history.csv`；
9. 不生成订单。

实时 Shadow 使用独立的网络失败边界：

- 单请求超时：`8` 秒；
- 单请求尝试次数：`1` 次；
- 成分股宽度整批超时：`60` 秒；
- 成分股宽度并发数：`16`；
- FRED 强制刷新失败时仅允许显式回退到已有缓存，并披露刷新状态；
- NDX 尾部仅允许在至少 3 个重叠日且最大差异不超过 0.02 点时追加；
- 请求失败或整批超时：进入 `DATA_GUARD`，不得猜测、补齐或生成交易动作。

### 14.3 Hermes

安装但不启用：

```bash
python3 integrations/hermes/install.py --no-enable
```

启用插件：

```bash
hermes plugins enable haven-market-model
```

不得在迁移验收阶段执行：

```bash
python3 integrations/hermes/install.py --update --create-cron --deliver telegram
```

---

## 15. Hermes 工具、接口和数据结构

### 15.1 工具

| 工具 | 输入 | 输出 | 副作用 |
|---|---|---|---|
| `haven_model_status` | `refresh:boolean=false`, `include_shadow:boolean=true` | 当前评分、覆盖率、状态、动作、Shadow | 可选刷新 |
| `haven_refresh_close` | 三个 force 布尔值，默认 true | 新收盘快照 | 写 v0.4 Shadow 输出 |
| `haven_screen_tqqq_calls` | `shares` 必填；成本、历史权利金、刷新、最大候选 | Gate、候选、Bid、Delta、DTE、有效卖价 | 不下单 |
| `haven_backtest_v04` | `rerun:boolean=false` | 指标、压力期、manifest | rerun 时重写研究输出 |

### 15.2 TQQQ Call 筛选输出字段

- `status`
- `symbol`
- `as_of`
- `spot`
- `shares`
- `whole_covered_contracts`
- `gate.status/eligible/reason`
- `expiry`
- `dte`
- `strike`
- `bid/ask`
- `seller_net_credit`
- `delta`
- `approx_assignment_probability`
- `open_interest`
- `volume`
- `relative_bid_ask_spread`
- `upside_to_strike`
- `premium_yield_on_spot`
- `effective_exit_price`
- `effective_exit_with_historical_premium`
- `rank_score`
- `automation_allowed=false`

### 15.3 典型工具输入

```json
{
  "shares": 1000,
  "cost_basis": 71.0,
  "historical_premium": 430.0,
  "refresh_model": false,
  "force_option_chain": true,
  "maximum_candidates": 5
}
```

### 15.4 典型工具输出

```json
{
  "status": "WAIT",
  "symbol": "TQQQ",
  "shares": 1000,
  "gate": {
    "eligible": false,
    "reason": "RANGE_CARRY requires G≥50.0, E≥40.0, and R_call≥55.0",
    "automation_allowed": false
  },
  "candidate_role": "contingency_watchlist_only",
  "research_only": true
}
```

即使返回候选，Gate 为 `WAIT` 时也不得解释为可执行交易。

### 15.5 收盘消息

```text
避风港 v0.4 收盘更新｜YYYY-MM-DD
P xx.xx / S xx.xx / G xx.xx / E xx.xx
R xx.xx (Put xx.xx / Call xx.xx) / I xx.xx / N SHADOW_NO_DATA
状态：STATE
动作：恐慌仓 ...；卖Put ...；卖Call ...；保险 ...。
口径：收盘计算，下一交易日生效。
边界：研究/Shadow only；不连接券商，不自动下单。
```

### 15.6 Cron 设计

代码支持但当前未创建：

```text
20 * * * *
```

含义是“每小时第 20 分钟”，不是“每 20 分钟”。

脚本过滤：

- 时区：`America/New_York`
- 工作日：周一至周五
- 时间窗：`16:15–17:30`
- `signal_date` 必须等于美东当前日期
- 同一 `signal_date` 成功投递后静默

两阶段账本：

1. 输出消息并写 `PENDING`：
   - `signal_date`
   - `written_at`
   - `job_id`
   - 当时 `last_run_at`
   - 当时 `last_delivery_error`
2. 下一次运行只在以下条件全部满足时写 `CONFIRMED`：
   - 唯一同名 job；
   - 相同 `job_id`；
   - `last_run_at` 推进；
   - `last_status == "ok"`；
   - `last_delivery_error is null`。
3. 状态未推进、重复任务名、任务缺失、投递错误或 Hermes 崩溃均重试。

---

## 16. 页面、图表和输出文件

### 16.1 页面

当前没有网页、Dashboard、移动页面或图形化交易页面。

### 16.2 图表

| 文件 | 内容 |
|---|---|
| `outputs/ten_year_v0_1/qqq_equity_drawdown.png` | QQQ 净值与回撤 |
| `outputs/ten_year_v0_1/tqqq_equity_drawdown.png` | TQQQ 净值与回撤 |
| `outputs/ten_year_v0_1/scores_states_weights.png` | 分数、状态和仓位 |
| `outputs/ten_year_v0_2_options_proxy/greed_call_activation.png` | Call 激活诊断 |
| `outputs/ten_year_v0_2_options_proxy/option_pnl_attribution.png` | 期权 P&L 归因 |
| `outputs/ten_year_v0_2_options_proxy/qqq_options_ablation.png` | QQQ 期权模块消融 |
| `outputs/ten_year_v0_2_options_proxy/tqqq_options_ablation.png` | TQQQ 期权模块消融 |

### 16.3 主要 CSV/JSON

- `score_history.csv`
- `state_history.csv`
- `panic_events.csv`
- `metrics.csv`
- `stress_periods.csv`
- `option_trades.csv`
- `option_summary.csv`
- `daily_qqq_full.csv`
- `daily_tqqq_full.csv`
- `recommended_metrics.csv`
- `recommended_option_summary.csv`
- `enriched_score_history.csv`
- `headline_comparison.csv`
- `options_module_ablation_metrics.csv`
- `current_snapshot.json`
- `live_shadow_history.csv`
- `nasdaq100_breadth_detail.csv`
- `qqq_option_chain_snapshot.csv`
- `x_topic_scores.csv`
- `run_manifest.json`

---

## 17. 当前技术架构和依赖

### 17.1 架构

```text
Nasdaq / FRED / Cboe / X
        ↓
data.py / enriched.py / live_market.py / x_shadow.py
        ↓
P/S/G/E/R/I/N 确定性评分
        ↓
state_machine
        ↓
backtest.py + options_proxy.py
        ↓
CSV / JSON / PNG
        ↓
Hermes tools + Skill + optional close message
```

语言模型只解释工具结果，不参与公式计算。

### 17.2 目录

| 路径 | 职责 |
|---|---|
| `config/` | v0.1–v0.4 参数 |
| `src/haven/` | 数据、评分、状态机、回测、期权、实时 Shadow |
| `scripts/` | 六个正式入口 |
| `tests/` | 37 项单元/集成测试 |
| `data/raw/` | 缓存原始数据和当前链 |
| `outputs/` | 回测、Shadow、图表和报告 |
| `integrations/hermes/` | Hermes 插件、Skill、CLI、安装和 Cron wrapper |
| `acceptance_bundle/` | 验收材料 |

### 17.3 运行依赖

`requirements.txt`：

```text
pandas>=2.2
numpy>=2.0
matplotlib>=3.8
PyYAML>=6.0
```

已验收 Hermes 环境：

- Python `3.11.15`
- Hermes `v0.13.x`
- Hermes commit `44cdf555a83c1d8d605d095442e11efd58089533`
- macOS 与 Linux

精确依赖锁文件：未实现；版本上限：待确认。

### 17.4 环境变量

| 变量 | 用途 | 必需 |
|---|---|---|
| `X_BEARER_TOKEN` | N 模型的 X 数据 | 否 |
| `HAVEN_RESEARCH_ROOT` | Hermes 指向引擎 | 适配器环境需要 |
| `HERMES_HOME` | Hermes 根目录 | 可选，默认 `~/.hermes` |
| `HAVEN_BACKTEST_TIMEOUT_SECONDS` | Hermes 回测超时 | 可选，默认 3600 |

不得提交密钥。

---

## 18. 核心伪代码

### 18.1 每日评分

```text
function BUILD_DAILY_SCORES(data_until_t, config):
    assert no rows after t are visible

    for each raw indicator:
        compute causal rolling return / MA / volatility / spread
        percentile = rolling_percentile(window=1260,
                                        min_periods=252 or 63)
        normalized = percentile or stress(percentile)

    for P, S, G, E, R_proxy:
        raw_score = weighted_average_of_available_components()
        coverage = available_nominal_weight
        base_score = SMA(raw_score, 3)

    compute breadth satellites with QQEW/QQQ
    compute liquidity satellites with publication lags
    compute tail satellites with VVIX/SKEW/VIX term structure

    P = COVERAGE_BLEND(P_base, breadth_stress,
                       liquidity_stress, tail_stress,
                       weights=[0.70,0.15,0.10,0.05])
    S = COVERAGE_BLEND(S_base, breadth_recovery,
                       liquidity_relief,
                       weights=[0.75,0.15,0.10])
    G = COVERAGE_BLEND(G_base, breadth_concentration,
                       100-tail_stress,
                       weights=[0.85,0.10,0.05])
    E = COVERAGE_BLEND(E_base, breadth_exhaustion,
                       tail_stress,
                       weights=[0.75,0.20,0.05])

    R_put = COVERAGE_BLEND(R_proxy, skew, vvix, term,
                           weights=[0.55,0.25,0.10,0.10])
    R_call = COVERAGE_BLEND(R_proxy, 100-skew, vvix, term,
                            weights=[0.65,0.15,0.10,0.10])
    R = COVERAGE_BLEND(R_put, R_call, weights=[0.50,0.50])

    risk_level = clip((P-20)/40*100, 0, 100)
    risk_acceleration = clip(max(P-P[t-5],0)/20*100, 0, 100)
    fragility = clip(0.60*E+0.40*G, 0, 100)
    I_need = 0.50*risk_level + 0.30*risk_acceleration + 0.20*fragility
    affordability = clip(100-R_put, 0, 100)
    I = 0.75*I_need + 0.25*affordability

    return scores, coverages, engine_native_audit
```

### 18.2 状态机

```text
function UPDATE_STATE(scores_t, memory):
    if P invalid or P_coverage < 0.80 or P_price missing:
        return DATA_GUARD

    if P >= 50:
        memory.trigger_streak += 1
    else:
        memory.trigger_streak = 0

    trigger = ((trigger_streak >= 2) or (P >= 70)) and (P_price >= 40)
    if trigger and not event_active:
        start new event
        event_low = close
        deployed_fraction = 0

    if event_active:
        update event_low
        if P < 30:
            exit_streak += 1
        else:
            exit_streak = 0
        if exit_streak >= 10:
            end event
            return normal_non_event_state

        require S valid and S_coverage >= 0.80

        early_gate =
            breadth_recovery >= 55 and
            liquidity_relief >= 30 and
            tail_stress <= 85

        retested_gate =
            breadth_recovery >= 60 and
            liquidity_relief >= 40 and
            tail_stress <= 75 and
            days_from_low >= 10

        if S >= 70 and close > MA20 and
           days_from_low >= 5 and retested_gate:
            candidate = RECOVERY_RETESTED
        else if S >= 70 and early_gate:
            candidate = RECOVERY_EARLY
        else if S >= 55:
            candidate = PANIC_STABILIZING_2
        else if S >= 35:
            candidate = PANIC_STABILIZING_1
        else:
            candidate = PANIC_ACCELERATING

        upgrade immediately
        downgrade only after 2 consecutive confirmations
        if new event low: accept candidate immediately

        allowed = deployment_fraction[candidate]
        if candidate == PANIC_ACCELERATING and P >= 80:
            allowed = max(allowed, 0.05)
        memory.deployed_fraction = max(previous_deployed, allowed)
        return candidate

    if 30 <= P < 50:
        return RISK_WARNING
    if G >= 60 and E >= 40:
        return GREED_EXHAUSTING
    if G >= 60:
        return GREED_TREND
    if RANGE_CONDITION:
        return RANGE_CARRY
    return NORMAL_PARTICIPATION
```

### 18.3 期权逐日循环

```text
function OPTION_BACKTEST_DAY(t):
    decision = state[t-1]
    apply base ETF return and cash return
    apply assigned inventory excess return

    for each existing option group:
        mark each leg at current theoretical Mid
        if expiry:
            settle at intrinsic
            if CSP ITM: create assigned lot
            if Call ITM: record assignment

    manage covered_call exits
    manage CSP 75% profit take
    manage insurance 20%/50% monetization
    preserve at least 50% insurance while risk active
    exit insurance only on risk-gone rule or DTE <= 10
    manage assigned lots

    if insurance entry gate passes and budget allows:
        buy protective puts at Ask
    else if CSP gate passes and no insurance:
        sell cash-secured puts at Bid
    else if Call gate passes and no insurance:
        sell covered calls at Bid

    record separately:
        base_pnl
        assigned_inventory_pnl
        covered_call_pnl
        cash_secured_put_pnl
        insurance_pnl
        execution_drag
        collected/paid premiums
        collateral
        effective asset weight
```

### 18.4 Cron 投递

```text
function CLOSE_UPDATE_WITH_LEDGER(now_et):
    if not weekday or not 16:15 <= time <= 17:30:
        return ""

    snapshot = refresh_close()
    if snapshot.signal_date != now_et.date:
        return ""

    if confirmed.signal_date == signal_date:
        return ""

    if pending.signal_date == signal_date:
        job = find_unique_job("Haven close update")
        if same_job_id and last_run_at_advanced and
           last_status == "ok" and last_delivery_error is null:
            promote pending to confirmed
            return ""

    job = find_unique_job()
    write pending(job_id, last_run_at, signal_date)
    return formatted_message
```

---

## 19. 已知问题、边界条件和风险

### 19.1 已确认问题

1. `acceptance_bundle/SUMMARY.md` 写 37 项测试，但 `test_report.txt` 仍保留旧的 30 项记录；
2. P/S/G/E 基础组件 Audit 的 `raw_indicator` 当前实际等于标准化值，不是真实原始市场量；
3. P/S/G/E Audit 同一列表中同时包含组件贡献和 `base_score` 汇总行，不能直接对整列 contribution 求和；
4. 账户级风险上限没有进入执行引擎；
5. `HARD_RISK_BREACH` 未实现；
6. 没有回撤熔断；
7. 依赖只有最低版本，没有 lockfile；
8. Pandas 对部分 `pct_change()` 默认填充方式发出 FutureWarning；
9. NFCI 有修订偏差；
10. 当前期权链和当前成分股有历史回填禁令；
11. 真实期权成交、提前行权、税务和保证金未建模；
12. TQQQ 历史价格调整更新会改变旧报告数字；
13. `recovery_s_min=101` 使 S 型保险恢复退出不可达；
14. Cron 代码已存在，但生产任务未创建，不能把“可创建”写成“已运行”。

### 19.2 模型风险

- v0.4 提高 Sharpe 的主要来源是减少 2022 假企稳加仓；
- 2020 V 型恢复收益受到恢复 Gate 拖累；
- CSP 是当前期权模块主要正贡献，但仍有大幅下跌与接货风险；
- 保险降低回撤，但长期净 P&L 为负；
- Covered Call 没有正贡献证据；
- 全样本固定参数可能存在过拟合；
- 真实链样本不足 30 个交易日；
- `N` 没有可用数据，不能验证新闻层。

---

## 20. 验收标准和测试用例

### 20.1 强制验收标准

1. 固定检出 `799624537aaf21a8a89fba309bb4498b792e5989` 或等价迁移提交；
2. `research_only=true`；
3. Paper/Live、券商和自动订单保持不存在；
4. Haven Cron 不创建、不启用；
5. 44 项现有测试全部通过；
6. 六个正式入口退出码全部为 `0`；
7. v0.3 指标复现到报告显示精度：
   - CAGR `10.4441%`
   - MDD `-24.2861%`
   - Sharpe `0.7519`
8. v0.4 指标复现到报告显示精度：
   - CAGR `11.4361%`
   - MDD `-16.4657%`
   - Sharpe `0.9492`
9. `R_put/R_call/R` contribution 重建误差 `≤1e-6`；
10. `I/I_need/I_affordability` contribution 不是 null；
11. `include_audit=false` 返回两项；
12. `include_audit=true` 返回三项；
13. `N_affects_position=false`；
14. 当前真实宽度和当前期权链不得进入历史回测；
15. 所有收盘信号延迟一个交易日；
16. TQQQ 和 Covered Call 不得进入 Paper/Live。

浮点跨平台完整精度容差：待确认。迁移首版使用第 7、8 项的四位显示精度作为现有验收口径。

### 20.2 必须保留的测试组

#### 模型

- 滚动百分位无未来依赖；
- I 分数无未来依赖；
- 恐慌启动和累计部署；
- 恢复 Gate 阻止过早满仓；
- 信号在下一交易日才生效。

#### v0.4 增强

- 宽度 DATA_GUARD；
- 增强分数无未来依赖；
- IV 正反推；
- N 永远 Shadow；
- 期权链使用观察报价；
- Put/Call 随 skew 分流；
- X 热度异常；
- 实时下载重试次数和单请求超时受控；
- 成分股宽度整批超时触发 `DATA_GUARD`；
- Audit 两项/三项兼容；
- I contribution 有效；
- R 全组件；
- ATM coverage 小于 1；
- tail 组件缺失；
- R 重建精度。

#### 期权

- Black-Scholes Put/Call parity；
- 到期 payoff 和 Delta；
- Covered Call 联合门槛；
- IV 输入无未来依赖；
- 月度到期日满足 DTE；
- Put/Call 使用不同 `R`；
- 100 股颗粒度。

#### Hermes

- 插件注册 4 工具、Skill 和命令；
- 状态工具保持 no-trade Gate；
- 低 `R_put` coverage 触发 DATA_GUARD；
- 收盘消息；
- 投递失败重试；
- 成功必须伴随 `last_run_at` 推进；
- Hermes 投递前崩溃必须重试；
- 重复 job name 必须安全失败；
- calculation_audit 回归。

#### 正式入口

- `run_ten_year.py`
- `run_options_proxy_v0_2.py`
- `run_insurance_model_v0_3.py`
- `run_insurance_recommended_v0_3.py`
- `run_enriched_indicators_v0_4.py`
- `run_live_shadow_v0_4.py`

### 20.3 新环境应补充但当前不存在的测试

以下为已发现缺口，不得标记为现有测试：

1. 账户 15%/6%/4% 风险上限真实否决；
2. `HARD_RISK_BREACH` 状态；
3. 最大回撤熔断；
4. P/S/G/E 基础 Audit 使用真实 raw market value；
5. Overlay 在 coverage 小于 1 时的 Audit 精确重建；
6. `recovery_s_min=101` 的意图确认；
7. Pandas `pct_change(fill_method=None)` 行为锁定；
8. 依赖版本锁定后的跨平台重现；
9. 真实期权链至少 30 日样本外验证；
10. Walk-forward 回测。

---

## 21. 典型输入输出

### 21.1 当前 Shadow 输出结构

```json
{
  "model": {
    "status": "NORMAL_PARTICIPATION",
    "signal_date": "2026-07-28",
    "effective_timing": "next trading day",
    "event_active": false,
    "deployment_fraction": 0.0,
    "scores": {
      "P": 24.550991637001026,
      "S": 46.908315788409965,
      "G": 25.19972883597886,
      "E": 53.5244411375661,
      "R_put": 67.15489655609417,
      "R_call": 67.19346675868417,
      "I": 21.31881796049097
    }
  },
  "real_breadth_shadow": {
    "status": "LIVE_SHADOW_READY",
    "historical_backtest_eligible": false
  },
  "real_qqq_chain_shadow": {
    "status": "LIVE_CHAIN_READY",
    "historical_backtest_eligible": false
  },
  "N_news_shadow": {
    "N": null,
    "N_status": "SHADOW_NO_DATA",
    "N_affects_position": false
  }
}
```

上述数值是 2026-07-29 独立复跑产生的时点示例，不是固定测试常量。

### 21.2 Audit 项

```json
{
  "name": "atm",
  "raw_indicator": 78.4656084656085,
  "data_date": "2026-07-28",
  "source": "atm",
  "transformation": "weighted blend (decomposition from _blend)",
  "normalized_value": 78.4656084656085,
  "nominal_weight": 0.55,
  "effective_weight": 0.461078,
  "contribution": 36.178754,
  "coverage": 1.0
}
```

精确重建必须使用内部未舍入 contribution；展示 JSON 可以保留 6 位小数。

---

## 22. 迁移实施顺序

1. 迁移原始文件和固定提交；
2. 建立 Python 3.11 虚拟环境；
3. 安装四项依赖；
4. 只运行测试，不刷新数据；
5. 复跑 v0.1；
6. 复跑 v0.2；
7. 复跑 v0.3；
8. 复跑 v0.4；
9. 比对固定基线；
10. 运行 Shadow；
11. 安装 Hermes 插件但不创建 Cron；
12. 验证 4 个工具；
13. 修复第 19 节缺口时建立新分支；
14. 在新的 Codex PASS 前不得启用 Cron、Paper、Live 或券商。

---

## 23. 必须一并迁移的源代码、文件、图片和数据

### 23.1 权威迁移集合

必须迁移 `EVIDENCE_COMMIT 7996245` 的全部 `286` 个 Git tracked 文件，并附带本规格书。迁移包内的 `MIGRATION_MANIFEST_SHA256.txt` 是逐文件权威清单和 SHA-256 校验表。

### 23.2 源代码

必须完整迁移：

- `src/haven/__init__.py`
- `src/haven/data.py`
- `src/haven/model.py`
- `src/haven/enriched.py`
- `src/haven/backtest.py`
- `src/haven/options_proxy.py`
- `src/haven/covered_call.py`
- `src/haven/live_market.py`
- `src/haven/x_shadow.py`
- `src/haven/reporting.py`
- `src/haven/reporting_v0_2.py`
- `scripts/run_ten_year.py`
- `scripts/run_options_proxy_v0_2.py`
- `scripts/run_insurance_model_v0_3.py`
- `scripts/run_insurance_recommended_v0_3.py`
- `scripts/run_enriched_indicators_v0_4.py`
- `scripts/run_live_shadow_v0_4.py`
- `integrations/hermes/` 全目录
- `tests/` 全目录

### 23.3 配置和项目文件

- `config/haven_v0_1.yaml`
- `config/haven_v0_2_options_proxy.yaml`
- `config/haven_v0_3_insurance_model.yaml`
- `config/haven_v0_4_enriched_indicators.yaml`
- `requirements.txt`
- `README.md`
- `.gitignore`
- `acceptance_bundle/` 全目录

### 23.4 原始数据

必须迁移 `data/raw/` 全目录，包括：

- Nasdaq ETF 行情与分红；
- Nasdaq-100 当前成员；
- `data/raw/nasdaq100_members/` 全部成员历史；
- QQQ/TQQQ 当前延迟期权链；
- FRED `NASDAQ100/BAMLH0A0HYM2/BAMLH0A3HYC/NFCI/DGS2/DGS3MO/DFII10`；
- Nasdaq 官方 NDX 历史尾部缓存（若实时 Shadow 已生成）；
- Cboe `VXN/VIX/VIX3M/VVIX/SKEW/VIX9D/VIX6M`。

### 23.5 回测结果和图表

必须迁移：

- `outputs/ten_year_v0_1/` 全目录；
- `outputs/ten_year_v0_2_options_proxy/` 全目录；
- `outputs/ten_year_v0_3_insurance/` 全目录；
- `outputs/ten_year_v0_4_enriched/` 全目录；
- 第 16.2 节列出的 7 张 PNG；
- 所有 CSV、JSON、Markdown 报告和交易日志。

### 23.6 Library 参考文档

迁移包还必须附带：

- `haven_full_cycle_strategy_v0.1.md`
- `haven_full_cycle_strategy_v0.2_options_proxy.md`
- `haven_options_proxy_backtest_report_v0.2.md`
- `haven_ten_year_backtest_report.md`
- `避风港全周期投资策略_总方案_v0.2.md`
- `避风港保险模型_v0.3.md`
- `避风港指标增强与N影子模型_v0.4.md`

### 23.7 不迁移

- `.git/` 对象库；
- `__pycache__/`
- `*.pyc`
- 本机密钥；
- `X_BEARER_TOKEN`
- Hermes 用户配置中的其他 Cron；
- Paper Broker 凭证；
- 其他项目的 `outputs/latest`；
- 未提交的临时下载和本机绝对路径。
