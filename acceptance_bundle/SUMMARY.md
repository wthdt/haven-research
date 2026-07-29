# Haven v0.4 Hermes Integration — Acceptance Bundle

## Codex Review: Round 2

### Status: All issues resolved, ready for Codex re-review

## Fixes Applied

1. **DATA_GUARD** — `_REQUIRED_SCORE_CODES` expanded from (P,S,G,E) to (P,S,G,E,R,R_put,R_call). I-related scores (I,I_need,I_affordability) checked for missing. Coverage < 80% for any required score → all actions BLOCKED. New test: `test_low_put_coverage_triggers_data_guard_blocks_sell_put` validates R_put_coverage=0.50 → sell_put=BLOCKED.

2. **SKILL.md** — `platforms: [linux]` → `[linux, macos]`. Verified via `hermes skills list` on macOS showing `haven-market-risk` as enabled/source=local.

3. **Install script** — `install.py` now copies skill to `~/.hermes/skills/haven-market-risk/`. Install.md/rollback.md updated for consistency.

4. **Cron** — Schedule changed from `20 * * * 1-5` to `20 * * * *`. Script uses America/New_York weekday + 16:15-17:30 window — works in Asia/Shanghai TZ. Fridays fully covered.

5. **Delivery ledger** — `close_update_once_with_ledger()` uses two-phase pattern: PENDING ledger written AFTER stdout delivery, CONFIRMED ledger promoted when Hermes job shows last_delivery_error=None AND last_run_at advanced.

6. **Cleanup** — `.gitignore` added, `__pycache__/*.pyc` removed from git.

7. **Engine-native calculation_audit** — The engine (`build_enriched_scores` in `src/haven/enriched.py`) now computes the audit natively with 3-day smoothing, v0.4 overlay breakdowns (P/S/G/E), R_put/R_call split, and I insurance components. The adapter (`_read_calculation_audit` in `tools.py`) retrieves it from the snapshot.

8. **Cron delivery with job-state binding** — `close_update_once_with_ledger()` binds the pending ledger to `job_id` and `last_run_at` from Hermes cron jobs.json. Confirmation requires the same job to have advanced `last_run_at` with `last_status=ok` and `last_delivery_error=None`. This is Hermes-crash-safe.

## Verified

- **30/30 tests pass** (25 original + 5 new cron/delivery tests)
  - test_low_put_coverage_triggers_data_guard_blocks_sell_put: new
  - test_calculation_audit_regression: new
  - test_close_message_and_deduplication: new (cron delivery)
  - test_delivery_failure_retry: new (cron delivery)
  - test_delivery_success_advances_last_run: new (cron delivery)
  - test_duplicate_job_name_fails_safe: new (cron delivery)
  - test_hermes_crash_before_delivery_retries: new (cron delivery)
- v0.3 baseline: CAGR 10.44%, MDD -24.29%, Sharpe 0.752
- v0.4 baseline: CAGR 11.44%, MDD -16.47%, Sharpe 0.949
- 4 tools registered in Hermes: haven_model_status, haven_refresh_close, haven_screen_tqqq_calls, haven_backtest_v04
- `/haven` command registered
- TQQQ uses Bid → correct WAIT return
- No secrets, broker connections, or outputs/latest writes
- Skill loads on macOS (real hermes skills list)
- DATA_GUARD blocks all actions on low coverage
- **calculation_audit**: non-empty P_overlay, S_overlay, G_overlay, E_overlay, R_put, R_call, R, I entries
- **Cron delivery**: all 7 scenarios verified (outside window, inside window, pending/retry, confirmed silent, crash no advance, duplicate job name, delivery success)

## Cron Status: **DISABLED** (not created, not enabled)

`hermes cron list` shows no "Haven close update" job. The job will be created by `install.py --create-cron --deliver telegram` after Codex approval.

## Tool Smoke Outputs

| File | Status |
|------|--------|
| haven_model_status.json | Valid JSON, non-empty calculation_audit (8 score breakdowns) |
| haven_refresh_close.json | Valid JSON, non-empty calculation_audit, includes data_metadata |
| haven_screen_tqqq_calls.json | Valid JSON, 5 TQQQ call candidates returned |
| haven_backtest_v04.json | Valid JSON, backtest metrics for v0.3/v0.4 baselines |
