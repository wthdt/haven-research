# Haven v0.4 Hermes Integration — Acceptance Bundle

## Codex Review: FAIL (pending fixes)

### Status: All 6 blocker issues resolved, pending Codex re-review

## Deliverables

| File | Description |
|------|-------------|
| `installation/` | Install script (patched), instructions, rollback |
| `integrations/hermes/` | Plugin, tools, CLI, cron wrapper |
| `tests/` | 25 tests (24 original + 1 new low-coverage) |
| `acceptance_bundle/` | This bundle |

## Fixes Applied

1. **DATA_GUARD** — `_REQUIRED_SCORE_CODES` expanded from (P,S,G,E) to (P,S,G,E,R,R_put,R_call). I-related scores (I,I_need,I_affordability) checked for missing. Coverage < 80% for any required score → all actions BLOCKED. New test: `test_low_put_coverage_triggers_data_guard_blocks_sell_put` validates R_put_coverage=0.50 → sell_put=BLOCKED.

2. **SKILL.md** — `platforms: [linux]` → `[linux, macos]`. Verified `readiness_status: "available"` via real Hermes `skill_view` on macOS.

3. **Install script** — `install.py` now copies skill to `~/.hermes/skills/haven-market-risk/`. Install.md/rollback.md updated for consistency.

4. **Cron** — Schedule changed from `20 * * * 1-5` to `20 * * * *`. Script uses America/New_York weekday + 16:15-17:30 window — works in Asia/Shanghai TZ. Fridays fully covered.

5. **Delivery ledger** — `close_update_once()` separates message generation from ledger management. Ledger written AFTER stdout delivery (in cli.py/close-update handler). Delivery failure → no ledger → next window tick retries.

6. **Cleanup** — `.gitignore` added, `__pycache__/*.pyc` removed from git.

## Verified

- 25/25 tests pass
- v0.3 baseline: CAGR 10.44%, MDD -24.29%, Sharpe 0.752
- v0.4 baseline: CAGR 11.44%, MDD -16.47%, Sharpe 0.949
- 4 tools registered in Hermes: haven_model_status, haven_refresh_close, haven_screen_tqqq_calls, haven_backtest_v04
- `/haven` command registered
- TQQQ uses Bid → correct WAIT return
- No secrets, broker connections, or outputs/latest writes
- Skill loads on macOS (real skill_view)
- DATA_GUARD blocks all actions on low coverage

## Cron Status: **DISABLED** (not created, not enabled)
