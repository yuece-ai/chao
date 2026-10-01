# QMT built-in strategy

The QMT client runs strategies as a single GBK-encoded Python 3.6 file. The
source stays as modules under `chao/`; `scripts/bundle_qmt.py` builds the
deployable file.

## Structure

| Module | Role | In bundle |
|---|---|---|
| `strategies/*.tdx` | one TDX formula per strategy; edit to tune | embedded |
| `chao/indicators.py` | MA, REF, HHV, LLV behind `INDICATORS` | yes |
| `chao/formulas.py` | parses formulas once and evaluates them; `bind()` maps data names | yes |
| `chao/settings.py` | fields, precedence, origins | yes |
| `chao/market.py` | symbols, `MarketData` interface, `Context` | yes |
| `chao/qfq.py` | forward adjustment verified against TDX | yes |
| `chao/signals.py` | strategies on one stock; universes; last-bar and history scans | yes |
| `chao/orders.py` | pure order planning for one shared account | yes |
| `chao/qmt_source.py` | `QmtMarket`: the only code that knows QMT's data API | yes |
| `chao/qmt_trade.py` | account reads, `passorder`, the ledger | yes |
| `chao/qmt_entry.py` | `init(C)` / `handlebar(C)` | yes |
| `chao/tdx_source.py`, `data.py`, `gbbq.py`, `replay.py`, `reference.py` | Linux TDX parity | no |

## Build and run

```sh
nix develop --command python scripts/bundle_qmt.py   # writes dist/chao_strategy.py
```

Before running:
1. Download the data in QMT (数据管理): daily bars for 沪深A股 and the
   indices the enabled strategies read (for strategies 1–6: `000001.SH`,
   `399001.SZ`, `399006.SZ`, `000688.SH`), ex-rights data and financial
   data. Keep the daily data current; live runs add only today's bar.
2. Copy `dist/chao_strategy.py` into QMT's strategy directory.
3. Set the main chart to daily (1d); the strategy refuses other periods.

Choose backtest or live in the QMT GUI; the strategy reads `C.do_back_test`.

- **Backtest:** at the first bar, signals inside the backtest window
  (`C.start`–`C.end`) are computed from QMT's local history in batches of
  200 stocks. Buy signals are kept as rows, sell signals as one boolean
  table per stock. Each bar then sends that day's orders to `passorder` with
  quickTrade 2, so they fill on the signal bar, and QMT simulates them. Fees
  and slippage come from QMT's backtest settings. If QMT's bars end before
  `C.end`, the run stops and asks for the history to be downloaded.
- **Live:** only on the last bar. On the first tick of each day, the
  universe and the per-stock static data (ex-rights, total shares, names)
  are read. Once a day at or after `trade_time`, the signals are computed
  and the orders are sent, or only printed while `dry_run` is on (the
  default).
  - `get_market_data_ex(subscribe=False)` reads local data, which ends
    yesterday during the session. Today's bar therefore comes from one
    `get_full_tick` call per batch: open, high, low, `lastPrice` as the
    close, and amount.
  - A stock with no tick today (suspended) gets no bar and no signal, and
    the run prints how many there were. If an index has no bar for today,
    the run stops.
  - The day is marked as attempted before the scan, so a failure is
    reported once rather than on every tick.

State lives in a module-level object. QMT rolls back attributes set on
`ContextInfo` between `handlebar` calls, so nothing is stored there.

The log goes to stdout (the QMT console), one line per event, prefixed `chao:`:
- the effective settings with their origins;
- signals;
- orders, marked `(dry-run)` when not sent;
- skipped stocks;
- the time taken by each phase.

## Order rules (`chao/orders.py`)

- **Selling:** a stock is sold only if chao bought it, i.e. it is in the
  ledger, so manual holdings are never touched. A sell uses the sellable
  volume, which respects T+1; frozen shares of a pending sell are not
  sellable.
- **Position limit:** at most `max_positions` chao stocks are held at once.
- **No re-buys:** a stock already in the account is never bought again.
  Neither is a stock with a chao order today, which keeps a restarted live
  strategy from repeating an unfilled buy.
- **Buy size:** each buy targets total assets / `max_positions`, capped by
  the remaining cash, minus 0.2% kept back for fees. Volumes are whole
  100-share lots; STAR (688/689) orders need at least 200 shares.
- **Conflicts:** when several signals arrive on the same day, `priority`
  decides. Buys are taken in (priority, symbol) order, and a stock signalled
  by several strategies belongs to the highest-priority one.
- **Ledger:** in a backtest it lives in memory. For live orders it is the
  JSON file at `ledger_path`, written after every placed buy, so a failure
  later in the loop cannot leave a bought stock untracked (and never sold).
  A symbol leaves the ledger once it is no longer held.

## Settings

Precedence: GUI parameter > account selected in QMT (the injected `account`
variable) > `CONFIG` in `chao/qmt_entry.py` > default. QMT's parameter panel
holds numbers only, so lists and text belong in `CONFIG`.

| Key | Default | Meaning |
|---|---|---|
| `strategies` | `1,2,3,4,5,6` | strategy ids to run; 7 (Beijing) is not traded |
| `priority` | `6,5,4,3,2,1` | conflict order, highest first; must list every strategy run |
| `sectors` | `沪深A股` | QMT sectors forming the universe |
| `account_id` | the QMT account | required in backtests and for live orders |
| `dry_run` | `1` | live only: print orders instead of sending them |
| `max_positions` | `10` | most chao stocks held at once |
| `trade_time` | `14:50` | live only: earliest time of day to trade |
| `ledger_path` | (empty) | required for live orders: JSON file of chao's symbols |

## QMT API use, checked against the official docs

Source: dict.thinktrader.net, 内置Python (innerApi): data, trading, system
functions, variable conventions and usage notes.

| Call | Documented behaviour relied on |
|---|---|
| `get_market_data_ex(fields, codes, period='1d', end_time, count, dividend_type='none', fill_data=False, subscribe=False)` | `{code: DataFrame}` indexed by `'YYYYMMDD'`; `count=-1` is every bar up to `end_time`; `subscribe=False` reads local data only, so it must be downloaded first |
| `get_divid_factors(code)` | `{epoch ms: [每股股利, 每股红股, 每股转增, 配股, 配股价, 是否股改, 除权系数]}`, amounts per share |
| `get_financial_data(['CAPITALSTRUCTURE.total_capital'], [code], start, end, report_type='announce_time')` | one stock over a range: DataFrame indexed by date, one column per field, in shares |
| `get_instrumentdetail(code)['InstrumentName']` | stock name; `get_stock_name` is slated for removal and returns GBK |
| `get_stock_list_in_sector(sector)` | list of `'600000.SH'` codes |
| `get_full_tick(codes)` | `{code: tick}` with `timetag`, `lastPrice`, `open`, `high`, `low`, `amount`; latest tick only, unusable in backtests |
| `get_bar_timetag(barpos)`, `barpos`, `period`, `do_back_test`, `start`, `end`, `is_last_bar()` | bar time in epoch ms; read-only run attributes |
| `get_trade_detail_data(account, 'STOCK', 'ACCOUNT'/'POSITION'/'ORDER', strategy)` | `m_dAvailable`, `m_dBalance`; `m_strExchangeID`, `m_strInstrumentID`, `m_nVolume`, `m_nCanUseVolume` |
| `passorder(23/24, 1101, account, code, 5, -1, volume, 'chao', 2, remark, C)` | buy/sell by shares at the latest price; quickTrade 2 sends on the current bar, also on historical bars |

Still to see in the client:
- the actual call latency;
- whether `get_market_data_ex` in a backtest returns bars past the current
  bar (the strategy requires bars up to `C.end` and stops otherwise);
- the bundled pandas version.

## Performance

Measured on Linux. The fake ContextInfo serves TDX data in QMT's shapes,
so QMT's own call latency is not included. The universe is about 5,100
沪深A股 stocks.

| Phase | Cost per stock | Whole universe | QMT calls |
|---|---|---|---|
| Live, first tick of the day: static data | 0.4 ms + 3 calls | ~2 s + call latency | ~15,000 |
| Live, at trade_time: 400 bars plus today's tick, 6 strategies | 14.5 ms | ~75 s + batch reads | 26 batched bar reads + 26 tick reads + 2–3 account reads |
| Backtest preparation: full history, 6 strategies | 31 ms | ~160 s + reads | 26 batched bar reads + 15,000 static reads |
| Backtest, per bar | — | under 1 ms + 2–3 account reads | 2–3 |

Memory:
- a backtest keeps sell tables of about 12 KB per stock for a 3.7-year
  window (about 60 MB in all), plus one batch of raw bars (about 50 MB);
- a live day keeps one batch of 400-bar frames.

Every phase is linear in stocks × bars. Since the formula optimisations
(formulas parsed once, data-only indicator calls shared across strategies
for each stock, comparisons and arithmetic on numpy arrays, a float fast
path for qfq rounding), one stock costs about 1/12 of the original time.
The TDX parity replay output is byte-identical.

Is this acceptable?
- **Live:** about 1.5–2 minutes from 14:50, leaving time before the 14:57
  closing call. QMT runs every strategy on one thread, so other strategies
  wait during that time. If the client's calls turn out slow, move
  `trade_time` earlier.
- **Backtest:** a few minutes of one-time preparation, then fast bars.

Known differences from the TDX parity replay:
- At 14:50, today's close and AMO (turnover) are intraday values. AMO is
  still short of the full day, so the AMO filters (e.g. `AMO>200000000`)
  pass less often than in a backtest that uses the full-day bar.
- TDX gives every stock its own 1,000,000; here all strategies share one
  account.
- The backtest universe is today's sector list, so delisted stocks are
  missing.
