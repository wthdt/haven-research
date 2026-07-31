# 避风港全周期策略研究回测

这是 `避风港全周期策略 v0.1` 的独立研究实现。它不会连接券商、不会写入
`outputs/latest`，也不会产生自动交易指令。

当前版本完成：

- QQQ/纳斯达克 100 市场环境识别；
- `P` 恐慌、`S` 企稳、`G` 贪婪、`E` 衰竭与权利金代理评分；
- 恐慌事件状态机与防抖；
- `35/20/45`、`50/15/35`、`60/10/30` 三组资金分层；
- QQQ 与 TQQQ 无期权版本的十年、无前视回测；
- 基准、危机分段、参数敏感性、事件日志和图表。

## v0.2 期权代理情景

`v0.2` 在保留 v0.1 状态机的前提下，新增：

- VXN 锚定的 Black-Scholes 每日期权估值；
- QQQ/TQQQ 独立 IV 缩放、期限结构和 Put skew；
- 100 股整数合约、买卖价差、滑点、费用和月度到期日；
- `G + E + R` 联合控制的 Covered Call；
- 现金担保 Put、到期指派和接货仓；
- Protective Put / Put Spread 保险、分批兑现与恢复退出；
- 模块消融、IV/点差、账户规模和保险结构敏感性。

它仍然是代理情景，不是历史期权链回放。真实执行价、成交量、持仓量和
美式提前行权无法由 VXN 与标的日线唯一恢复，因此结果必须按区间解释。

当前版本不做：

- 不把理论成交冒充真实历史期权链；
- 不连接券商或生成实盘订单；
- 不进入 Paper/Live。

## v0.3 保险模型

`v0.3` 把保险从执行模块升级为第六模型 `I`，分成
`I_need / I_affordability / I`。当前研究参数为约90日、
Delta约 `-0.20` 的 Protective Put，单次净保费上限为策略净值
`0.30%`，全年硬上限为 `1.00%`。

## v0.4 指标增强与 N 影子层

`v0.4` 新增但不自动进入实盘：

- `QQEW/QQQ` 纳指等权宽度代理；
- CCC与高收益债利差、NFCI、2年期利率与10年实际利率冲击；
- Cboe `VVIX / SKEW / VIX9D / VIX6M`；
- `R_put / R_call` 两条独立权利金评分线；
- 恐慌恢复的宽度、流动性与尾部风险确认门槛；
- `E > 55` 时禁止新卖现金担保 Put；
- Nasdaq-100当前成分股真实宽度影子快照；
- QQQ延迟期权链的30/90日IV、25Delta偏斜、价差、成交量与持仓量；
- `N` 新闻/X影子接口；未配置X授权时必须返回数据保护状态。

当前成分股宽度和真实期权链从接入日起积累，不允许回填为历史数据。
`N` 只观察，不改变仓位。

### 实时 Shadow 的网络失败边界

实时入口使用 `config/haven_v0_4_enriched_indicators.yaml` 中的
`live_shadow.network` 控制外部请求：

- 单请求超时：8 秒；
- 单请求尝试次数：1 次；
- 当前成分股宽度整批超时：60 秒；
- 宽度并发数：16。

请求失败或整批超时必须进入 `DATA_GUARD`，不得用猜测值补齐，也不得因此
生成交易动作。研究回测的默认下载参数保持不变。

实时 Shadow 对 NDX 收盘价采用一条受控的尾部补全路径：FRED
`NASDAQ100` 仍是历史权威序列；仅当 Nasdaq 官方 NDX 历史接口与 FRED
至少有 3 个重叠交易日、且收盘价最大绝对差不超过 0.02 点时，才追加
FRED 最新日期之后的 Nasdaq 收盘价，不覆盖任何 FRED 历史值。强制刷新时
若 FRED 暂时不可访问，可显式使用已有缓存继续计算，并在快照
`refresh_status` 中披露；缓存不存在、重叠不足或数值不一致仍进入
`DATA_GUARD`。

## 运行

```bash
cd haven_research
python3 scripts/run_ten_year.py
python3 scripts/run_options_proxy_v0_2.py
python3 scripts/run_insurance_recommended_v0_3.py
python3 scripts/run_enriched_indicators_v0_4.py
python3 scripts/run_live_shadow_v0_4.py
python3 -m unittest discover -s tests -v
```

默认回测区间为 `2016-07-25` 至 `2026-07-24`。所有信号在交易日收盘后
计算，目标仓位延迟一个交易日生效。

## Hermes Agent 适配

`integrations/hermes/` 提供 NousResearch Hermes Agent 的原生插件、
只读 Skill、`/haven` 命令和无 LLM 收盘任务。插件工具包括：

- `haven_model_status`：读取或刷新七模型状态；
- `haven_refresh_close`：运行正式收盘影子快照；
- `haven_screen_tqqq_calls`：按卖方 Bid、Delta、DTE、价差和持仓成本筛选；
- `haven_backtest_v04`：读取或重跑 v0.4 回测。

在 Hermes 所用的 Python 环境中运行：

```bash
python3 integrations/hermes/install.py --no-enable
hermes plugins enable haven-market-model
```

如需把收盘更新发送到 Telegram：

```bash
python3 integrations/hermes/install.py \
  --update \
  --create-cron \
  --deliver telegram
```

Cron 每个工作日每小时的第20分钟检查一次，但只有美东时间
`16:15–17:30`、正式交易日期更新且尚未发送时才输出，因此不依赖服务器
本地时区，也不会在休市日重复旧读数。所有 Hermes 接口仍保持
`research/Shadow only`，不连接券商或生成实盘订单。

## 数据

- QQQ、TQQQ、RSP、SPY、HYG、IEF、BIL：Nasdaq 历史行情和分红；
- 纳斯达克 100：FRED `NASDAQ100` 历史序列；实时尾部经重叠校验后使用
  Nasdaq 官方 NDX 历史收盘；
- 信用代理：HYG/IEF；FRED `BAMLH0A0HYM2` 只作近年诊断；
- 现金收益：FRED `DGS3MO` 滞后一个交易日并扣除 BIL 近似费率；
- VXN、VIX、VIX3M：Cboe 官方历史文件。

下载后的原始数据保存在 `data/raw/`，回测结果保存在
`outputs/ten_year_v0_1/`；期权代理结果保存在
`outputs/ten_year_v0_2_options_proxy/`。

## 重要口径

资金比例针对一个独立的策略 sleeve，而不是整个账户。账户级的 15% 标准
压力损失、6% 普通风险因子上限、4% 杠杆 ETF 上限，需要结合 sleeve 占
整个账户的比例再判断。报告会给出风险换算，但不会把未经确认的账户比例
写死进收益回测。
