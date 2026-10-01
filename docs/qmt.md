# QMT built-in strategy

The QMT client runs the strategies as a single GBK-encoded Python 3.6 file.
The source stays as modules under `chao/`; `scripts/bundle_qmt.py` builds
the deployable file.

## Structure

| Module | Role | In bundle |
|---|---|---|
| `strategies/*.tdx` | one TDX formula per strategy; edit to tune | embedded |
| `chao/indicators.py` | MA, REF, HHV, LLV behind `INDICATORS` | yes |
| `chao/formulas.py` | parses and evaluates formulas; `bind()` maps data names | yes |
| `chao/settings.py` | fields, precedence, origins | yes |
| `chao/market.py` | symbols, `MarketData` interface, `Context` | yes |
| `chao/qfq.py` | forward adjustment verified against TDX | yes |
| `chao/signals.py` | one strategy on one stock; strategy universes | yes |
| `chao/qmt_source.py` | `QmtMarket`: the only code that knows QMT's API | yes |
| `chao/qmt_entry.py` | `init(C)` / `handlebar(C)` | yes |
| `chao/tdx_source.py`, `data.py`, `gbbq.py`, `replay.py`, `reference.py` | Linux TDX parity | no |

## Build and deploy

```sh
nix develop --command python scripts/bundle_qmt.py   # writes dist/chao_strategy.py
```

Copy `dist/chao_strategy.py` into QMT's strategy directory and run it in
simulation mode. On the last bar, `handlebar` prints:
- the effective settings, each with its origin;
- every signal, as `chao: signal <date> strategy=<id> <symbol> buy|sell`;
- every stock skipped for missing data.

It places no orders. A sell line is the raw sell condition; it does not
check whether a position exists.

## Settings

Precedence: GUI parameter > `CONFIG` in `chao/qmt_entry.py` > default.

| Key | Default | Meaning |
|---|---|---|
| `strategies` | `1,2,3,4,5,6,7` | strategy ids to run |
| `sectors` | `沪深A股,京市A股` | QMT sectors forming the universe |

A GUI parameter is used when the strategy's formulaLayout xml defines a
parameter with the same name. The xml is not generated yet, because its
schema needs a sample from the client.

## Not yet confirmed in the client

These follow the QMT documentation and are tested only against a fake
ContextInfo. `QmtMarket` raises an error on any other shape.
- `get_divid_factors(code)` returns `{date: [interest, stockBonus, stockGift,
  allotNum, allotPrice, gugai, dr]}`, with amounts per share.
- `get_financial_data(['CAPITALSTRUCTURE.total_capital'], [code], ...)` for
  one stock returns a date-indexed Series of shares.
- The sector names `沪深A股` and `京市A股`, and the index codes `000001.SH`
  and `899050.BJ`.
- The client's pandas and numpy versions.

On Linux, `tests/test_local_data.py` feeds `QmtMarket` TDX bars and GBBQ in
these shapes. It reproduces the TDX path's adjusted bars and signals for 14
reference stocks.
