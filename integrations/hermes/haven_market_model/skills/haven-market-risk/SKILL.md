---
name: haven-market-risk
description: Use the deterministic Haven v0.4 P/S/G/E/R/I/N model to answer current market-state, panic/recovery, premium, insurance, and TQQQ covered-call questions. Use the plugin tools for every numeric score, current-state update, backtest, or option candidate; never recreate scores from prose or guess live quotes.
version: 0.4.0
author: zizi
license: Proprietary
platforms: [linux]
metadata:
  hermes:
    tags: [Finance, Risk, Options, TQQQ, Nasdaq]
    requires_tools:
      - haven_model_status
---

# Haven Market Risk

Use the plugin as the source of truth. Keep the engine deterministic and the
language model interpretive.

## Procedure

1. For current `P/S/G/E/R/I/N` values, call `haven_model_status`.
2. Set `refresh=true` only after the US close or when the user explicitly asks
   for fresh data. Otherwise report the latest completed close and its date.
3. Lead with `DATA_GUARD` whenever the tool reports it. Do not derive a signal
   from partial data.
4. Apply decisions in this order:
   `DATA_GUARD` → hard risk breach → panic/recovery state → risk warning →
   greed/exhaustion/premium.
5. For TQQQ Calls, call `haven_screen_tqqq_calls` with the actual share count,
   cost basis, and realized historical premium when known. Use seller Bid, not
   Ask, when describing obtainable premium.
6. For historical evidence, call `haven_backtest_v04`. Rerun only when asked.

## Output

Report:

- signal date and next-trading-day effective timing;
- each score, coverage, and state;
- the model action for panic reserve, Put, Call, and insurance;
- the reason a gate is open or closed;
- real breadth and option-chain shadow status when relevant.

For a Call candidate, include expiry, strike, seller net credit, Delta, DTE,
spread, open interest, upside to strike, effective exit price, and assignment
economics relative to cost.

## Guardrails

- Keep v0.4 in research/Shadow mode.
- Never place, stage, or construct a broker order.
- Never promote TQQQ or Covered Call to Paper/Live.
- Treat current constituent breadth and delayed option chains as live snapshots;
  never backfill them into history.
- Keep `N` non-executable for at least 30 live days. Missing X authorization must
  remain `SHADOW_NO_DATA`, not be imputed.
- Do not let the same cash simultaneously back a cash-secured Put and the panic
  reserve.
- Preserve the close-to-next-trading-day signal delay.
