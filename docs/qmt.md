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
| `chao/signals.py` | one strategy on one stock; strategy universes; `scan()` | yes |
| `chao/orders.py` | pure order planning for one shared account | yes |
| `chao/qmt_source.py` | `QmtMarket`: the only code that knows QMT's API | yes |
| `chao/qmt_trade.py` | account reads, `passorder`, the ledger | yes |
| `chao/qmt_entry.py` | `init(C)` / `handlebar(C)` | yes |
| `chao/tdx_source.py`, `data.py`, `gbbq.py`, `replay.py`, `reference.py` | Linux TDX parity | no |

## Build and deploy

```sh
nix develop --command python scripts/bundle_qmt.py   # writes dist/chao_strategy.py
```

Copy `dist/chao_strategy.py` into QMT's strategy directory. Choose backtest
or live in the QMT GUI; the strategy reads `C.do_back_test`.

- **Backtest:** at the first bar, every bar's signals are computed once from
  QMT's local history. Each bar then plans that day's orders and sends them
  to `passorder`, and QMT simulates the fills. Fees and slippage come from
  QMT's backtest settings.
- **Live:** only on the last bar, once a day at or after `trade_time`. With
  `dry_run` on (the default), orders are printed and nothing is sent. With
  `dry_run` off, orders go to `passorder` at the latest price.

The log goes to stdout (the QMT console), one line per event, prefixed `chao:`:
- the effective settings with their origins;
- signals;
- orders, marked `(dry-run)` when not sent;
- stocks skipped for missing data.

## Order rules (`chao/orders.py`)

- **Selling:** a stock is sold only if chao bought it, i.e. it is in the
  ledger, so manual holdings are never touched. A sell uses the sellable
  volume, which respects T+1.
- **Position limit:** at most `max_positions` chao stocks are held at once.
  A stock already in the account is never bought again.
- **Buy size:** each buy targets total assets / `max_positions`, capped by
  the remaining cash, minus 0.2% kept back for fees. Volumes are whole
  100-share lots; STAR (688/689) orders need at least 200 shares.
- **Conflicts:** when several signals arrive on the same day, `priority`
  decides. Buys are taken in (priority, symbol) order, and a stock signalled
  by several strategies belongs to the highest-priority one.
- **Ledger:** in a backtest it lives in memory. For live orders it is the
  JSON file at `ledger_path`. A symbol leaves the ledger once it is no
  longer held.

## Settings

Precedence: GUI parameter > `CONFIG` in `chao/qmt_entry.py` > default.

| Key | Default | Meaning |
|---|---|---|
| `strategies` | `1,2,3,4,5,6` | strategy ids to run; 7 (Beijing) is not traded |
| `priority` | `6,5,4,3,2,1` | conflict order, highest first; must list every strategy run |
| `sectors` | `沪深A股` | QMT sectors forming the universe |
| `account_id` | (empty) | required in backtests (any name, e.g. `testS`) and for live orders |
| `dry_run` | `1` | live only: print orders instead of sending them |
| `max_positions` | `10` | most chao stocks held at once |
| `trade_time` | `14:50` | live only: earliest time of day to trade |
| `ledger_path` | (empty) | required for live orders: JSON file of chao's symbols |

A GUI parameter is used when the strategy's formulaLayout xml defines a
parameter with the same name. The xml is not generated yet, because its
schema needs a sample from the client. QMT's parameter panel may accept only
numbers. If so, list values such as `strategies` must be set in `CONFIG`.

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
- The `passorder` quickTrade value: 0 in backtests (QMT's standard bar
  handling) and 2 live (immediate). Whether a backtest fills on the signal
  bar or the next one needs checking in the client.
- `get_trade_detail_data` in backtests, and whether `m_strExchangeID` is
  `SH`/`SZ`.

Known differences from the TDX parity replay:
- TDX gives every stock its own 1,000,000; here all strategies share one
  account.
- The backtest universe is today's sector list, so delisted stocks are
  missing.

On Linux, `tests/test_local_data.py` feeds `QmtMarket` TDX bars and GBBQ in
these shapes. It reproduces the TDX path's adjusted bars and signals for 14
reference stocks.
