# Round 4 EVIDENCE_COMMIT — R_put/R_call engine-native decomposition fix

## Code
- Commit: 62d00e2
- Parent: 0667eb1 (acceptance bundle v3)
- Changes: src/haven/enriched.py, tests/test_enriched.py (+285/-98 lines)

## Audit Reconstruction Errors
- R_put: 0.00e+00 (exact match)
- R_call: 0.00e+00 (exact match)
- R: 7.11e-15 (FP rounding, << 1e-6)

## Audit Coverage
- R_put: 4 entries (atm, skew, vol_of_vol, term)
- R_call: 4 entries (atm, inverse_skew, vol_of_vol, term)
- R: 2 entries (put, call legs)
- I: 2 entries (I_need, affordability)
- I_need: 3 entries (risk_level, risk_acceleration, fragility)
- I_affordability: 1 entry (affordability)

## include_audit Flag
- False: returns (DataFrame, dict) — 2 items
- True: returns (DataFrame, dict, dict) — 3 items

## Tests (37/37 passed)
- test_enriched.py: 14 tests (8 new audit-specific tests)
- test_model.py: 5 tests
- test_options_proxy.py: 7 tests
- test_covered_call.py: 3 tests
- test_hermes_adapter.py: 8 tests

## Six Entry Points (all exit 0)
1. run_enriched_indicators_v0_4.py — ✅
2. run_insurance_model_v0_3.py — ✅
3. run_insurance_recommended_v0_3.py — ✅
4. run_options_proxy_v0_2.py — ✅
5. run_live_shadow_v0_4.py — ✅
6. run_ten_year.py — ✅

## Baseline Results (unchanged from 0667eb1)
- v0.3 (QQQ_Haven_35_20_45, full_proxy): CAGR=10.4441%, MDD=-24.2861%, Sharpe=0.7519
- v0.4 (QQQ_Haven_50_15_35, full_enriched_scores): CAGR=11.4361%, MDD=-16.4657%, Sharpe=0.9492

## Cron Status
Haven-related cron jobs: DISABLED (no haven cron entries found)
Unrelated crons present: A-share weekly, micro-cap pharma, triple strategy shadow, paper-broker

## Production Impact
- No changes to production, Paper/Live, or broker
- research_only=True
- No option chain or broker orders sent
