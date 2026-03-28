# QA Report — Import & Unit Tests
**Date:** 2026-03-28
**Project:** Gold Trading Bot (`/Users/luan_prep_vn/Desktop/v-matrix/trade-gold`)
**Python:** 3.12.6 | **Pytest:** 9.0.2

---

## Environment Setup

| Step | Result |
|---|---|
| venv created | OK (new) |
| `requirements.txt` install | PARTIAL — `pandas-ta==0.3.14b0` not on PyPI; installed `0.4.67b0` instead |
| Dependency conflict | `pandas-ta 0.4.67b0` requires `pandas>=2.3.2`, pinned `pandas==2.2.2` |
| pytest install | OK (present in venv) |

**Action required:** Update `requirements.txt` — change `pandas-ta==0.3.14b0` to `pandas-ta==0.4.67b0` and `pandas==2.2.2` to `pandas>=2.3.2`.

---

## Unit Tests (tests/test_risk.py)

**25 collected | 23 PASSED | 2 FAILED**

### FAILED: `TestCalcPositionSize::test_leverage_caps_position`

**Root cause:** `calc_position_size` rounds qty to 3 decimal places *after* capping notional, allowing the rounded value to exceed `balance × leverage` by a fraction.

- Inputs: balance=10000, risk_pct=1%, entry=3300, SL=3299, leverage=5
- Capped `position_value = 50000`, `quantity = 50000/3300 = 15.1515…`
- `round(15.1515, 3) = 15.152` → notional = **50001.60** > 50000

**Fix:** Apply `math.floor(quantity * 1000) / 1000` (floor-round, not round-half-up) before returning, or cap the final quantity: `quantity = min(quantity, max_position_value / entry)` *after* rounding.

---

### FAILED: `TestValidateSignalPrices::test_exactly_at_boundary`

**Root cause:** Test assumes "1% deviation on entry price == boundary", but the implementation computes `deviation = |entry - mark| / mark` (mark as denominator). At exactly 1% below entry, `mark = entry × 0.99`, so:

```
deviation = (entry - mark) / mark = entry×0.01 / (entry×0.99) = 0.01/0.99 = 0.010101… > 0.01
```

Code correctly rejects it; test expectation is wrong.

**Fix (choose one):**
- Option A — Fix *test*: set `mark = entry / 1.01` so deviation against mark is exactly 0.01.
- Option B — Fix *implementation*: use `entry` as denominator (`deviation = |entry - mark| / entry`) for a symmetric check.

---

## Import Tests (35 modules)

**32 PASS | 3 FAIL**

### FAIL: `macro` — `bot.data.macro`

```
ImportError: cannot import name 'get_macro_context' from 'bot.data.macro'
```

`macro.py` only defines `fetch_fred_data` and `fetch_news`. `get_macro_context` does not exist.
Cascades to `orchestrator` failure below.

---

### FAIL: `tg_handlers` — `bot.telegram.handlers`

```
ImportError: cannot import name 'cmd_start' from 'bot.telegram.handlers'
```

`bot/telegram/handlers/` is an **empty package** (`__init__.py` has only a comment). All handler functions (`cmd_start`, `cmd_status`, `cmd_signal`, etc.) live in the **flat file** `bot/telegram/handlers.py`.

Python resolves the package first, shadowing the flat file.

**Fix:** Remove (or merge) the `bot/telegram/handlers/` directory, leaving only `bot/telegram/handlers.py`.

---

### FAIL: `orchestrator` — `bot.orchestrator`

```
ImportError: cannot import name 'get_macro_context' from 'bot.data.macro'
```

Direct consequence of the `macro` failure. `orchestrator.py` imports `get_macro_context` which does not exist.

---

## Functional Tests (no external API)

| Test | Result | Notes |
|---|---|---|
| Config loading (mock env) | PASS | `mode=swing, paper=True, testnet=True` |
| Timezone utils | PASS | UTC/ICT conversion, session label correct |
| Risk — `calc_position_size` | PASS | 1.5620 oz for $5000 @ 1%, SL=$20, lev=1 |
| Risk — `validate_signal_sl` | PASS | Returns True for expected ATR distance |
| Cycle context | PASS | March 2026, NEUTRAL seasonal, Saturday |
| FilterResult model | PASS | `passed=True, ratio=1.2` |

**Note:** `calc_position_size` test above was called with `leverage=1` (not the original script's signature-mismatch call). The original test script used the wrong interface — `leverage` is a required positional arg, not optional.

---

## Summary

| Category | Count |
|---|---|
| Import tests PASS | 32 / 35 |
| Import tests FAIL | 3 / 35 |
| Unit tests PASS | 23 / 25 |
| Unit tests FAIL | 2 / 25 |
| Functional smoke tests PASS | 6 / 6 |

---

## Critical Issues (blocking)

1. **`get_macro_context` missing** — `bot.data.macro` lacks this function. `orchestrator` is broken at import time; the bot cannot run.
2. **`handlers` package shadows flat file** — all Telegram command handlers are unreachable via `from bot.telegram.handlers import cmd_start`.

## Non-Critical Issues

3. `pandas-ta==0.3.14b0` unavailable on PyPI — breaks fresh installs.
4. `calc_position_size` rounding allows notional to exceed leverage cap by ~$1.60 (edge case, tight SL).
5. `test_exactly_at_boundary` uses wrong denominator assumption — test is incorrect, not the code.

---

## Recommendations

| Priority | Action | File |
|---|---|---|
| P0 | Add `get_macro_context` function to `macro.py` (or fix import alias) | `bot/data/macro.py`, `bot/orchestrator.py` |
| P0 | Remove `bot/telegram/handlers/` empty package directory | `bot/telegram/handlers/` |
| P1 | Pin `pandas-ta==0.4.67b0` and `pandas>=2.3.2` in requirements | `requirements.txt` |
| P1 | Floor-round qty in `calc_position_size` to avoid notional overage | `bot/risk/calculator.py` |
| P2 | Fix `test_exactly_at_boundary`: use `mark = entry / 1.01` | `tests/test_risk.py:194` |
| P2 | Add `leverage` default value in `calc_position_size` (currently required, breaks original test script) | `bot/risk/calculator.py` |

---

## Unresolved Questions

- Was `get_macro_context` renamed to something else (e.g. `get_macro_data`, `fetch_macro`)? The orchestrator references it but it's absent from `macro.py` — possible it was deleted during refactor.
- Should `handlers/` package be kept for future sub-module expansion, or is it an accidental artifact?
- Is `pandas==2.2.2` a hard requirement (tested against specific API), or can it be bumped to `>=2.3.2`?
