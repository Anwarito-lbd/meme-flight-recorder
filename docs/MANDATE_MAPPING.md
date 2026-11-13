# Mandate → Code Traceability

The operator supplied an external "Memecoin Trading Execution Mandate" and asked
that the agents act on it. Audit result 2026-08-22: **the overwhelming majority
of that mandate is already enforced by this codebase** — as deterministic code,
which is the stronger form. This document maps every clause to its
implementation or to the measured result that overrode it.

| Mandate clause | Status | Where / evidence |
|---|---|---|
| 3 strictness levels, switchable | ✅ BUILT | `[strictness.*]` config + `cli scout --level`; confluence minimums 4/3/2 match exactly |
| Ask before trading if level unspecified | ✅ BUILT | scout `--level` has no default and stops without one |
| Parameter questionnaire; never assume missing values | ✅ BUILT | every `[scout.filters]` param required; missing value stops the run (`CLAUDE.md`) |
| 48h max token age | ✅ BUILT | `maximum_token_age_hours = 48`, verified before analysis |
| Minimum volume floor | ✅ BUILT | `minimum_volume_usd`; "no volume means no buyers" encoded |
| Min/max market-cap band | ✅ BUILT | rejected 30 candidates on `market_cap_band` in a single cycle today |
| First-1m scam-pump filter | ✅ BUILT | `maximum_first_candle_multiple` — 12 vertical-candle rejects today |
| No re-entry ("double dipping") | ✅ BUILT | `forbid_reentry` + `held_mints` state veto in `scout.py` |
| Confluence ≥N signals to enter | ✅ BUILT | `confluence.py`: PRESENT/ABSENT/**UNKNOWN** states; unknowns can never count |
| Volume expansion signal | ✅ RESOLVES | candle baseline ratio (resolves on 2 of 8 live) |
| Buy-side imbalance | ✅ RESOLVES | `buy_transaction_share_pct` from feed txn counts |
| Whale accumulation | ⏳ SPEC'D | field exists; resolver spec'd 2026-08-22, needs study before counting |
| Narrative acceleration | ✅ PARTIAL | topic net-inflow resolves; media-mention velocity spec'd 2026-08-22 |
| Bonding-curve acceleration | ⏳ STUDY EXISTS | `study_bonding_acceleration.py`; field plumbed, unproven |
| Hard stop-loss every trade | ❌ OVERRIDDEN BY DATA | replayed collapse fell **1,700× past its stop inside one candle**; sizing is the stop here. CEX engine uses real stops where books are deep |
| Partial TP 1.5R / trail 2R | ⏳ TESTABLE | `simulate_tp_levels.py` + `compare_exit_policies.py` exist; capping upside removes the measured tail |
| Daily loss cap | ✅ BUILT | `[risk] maximum_daily_loss_pct = 1.0` |
| Stop after N consecutive losses | ✅ BUILT | `[risk] consecutive_loss_limit = 3` |
| No averaging down / martingale | ✅ BUILT | no such path exists anywhere in the book |
| Liquidity sweep / VWAP / higher-low | ✅ DEFINED, expected UNKNOWN | tokens <48h have no chart — honest UNKNOWN beats invented values |
| LLM bounded analyst, never signs | ✅ BUILT | sentiment backend swappable; model ranks/explains only; `test_no_live_execution.py` green |
| Exit quoted & stress-tested before buy | ✅ BUILT | Jupiter round-trip evidence gates every candidate |

## The part the mandate gets wrong here

"Too many fails" is the filter succeeding: 61–63 of ~77 candidates per cycle die
because most launchpad tokens ARE rugs or dust (84% vanish within days;
median launch pool $26). When 13 winners were freed from pure data-gaps, the
system caught them; when a gate was tested against outcomes, it kept or fixed
itself (deployer band inverted; concentration validated at 55%/38%).

New signals earn entry by the same ladder everything else climbed:
journal → study → decision rule written first → THEN count toward confluence.
