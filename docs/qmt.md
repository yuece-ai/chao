# QMT built-in strategy

The QMT client runs a strategy as a single Python 3.6 file. The source stays
as modules under `chao/`; `scripts/bundle_qmt.py` builds the deployable file.
The file is pure ASCII: Chinese text in strings is written as `\u` escapes
with the same values at run time, so it can be pasted into QMT's strategy
editor whatever encoding the copy goes through.

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

Settings go in a local `qmt.json` (copy `qmt.example.json`; it holds the
account, so it is not committed). The bundler checks it against the fields
below and writes it into the file's `CONFIG`:

```sh
nix develop --command python scripts/bundle_qmt.py --config qmt.json   # writes dist/chao_strategy.py
```

Rebuild after changing `qmt.json`; never edit the generated file.

The ready-to-paste build is committed as `qmt/chao_strategy.py`, built from
`qmt/strategy.json`. Nothing is edited in the client: the run mode follows
the button pressed (回测 or 运行, read from `C.do_back_test` at the first
bar), the account is the one selected in QMT (falling back to `testS` for
backtests), the backtest range comes from QMT's backtest settings, trade
lists go to `D:\chao\report` and the ledger to `D:\chao\ledger.json`, with
`dry_run` on. A test keeps it identical to a fresh build. After changing
the sources, rebuild it:

```sh
nix develop --command python scripts/bundle_qmt.py --config qmt/strategy.json --out qmt/chao_strategy.py
```

Before running:
1. Download the data in QMT (数据管理): daily bars for 沪深A股 and the
   indices the enabled strategies read (for strategies 1–6: `000001.SH`,
   `399001.SZ`, `399006.SZ`, `000688.SH`), ex-rights data and financial
   data (财务数据: FINANCE(1) reads the total-share history). A live run
   requests the incremental daily download itself each morning; refresh
   ex-rights and financial data weekly by hand.
2. Paste `qmt/chao_strategy.py` into a QMT strategy.
3. Set the main chart to daily (1d); the strategy refuses other periods.

Choose backtest or live in the QMT GUI; the strategy reads `C.do_back_test`.

- **Backtest, aligned with the user's TDX backtest:**
  - At the first bar, every (strategy, stock) pair is replayed with the TDX
    ledger (`chao/replay.py`, the rules verified in PARITY.md): its own
    `tdx_cash`, fills at the close, TDX fees (0.05% buy, 0.03% sell), float32
    money, buy before sell on one bar, flatten on the last bar.
  - The trades go to `report_path` in the TDX export layout, one
    `strategy-<id>-signals-<run time>.tsv` per strategy, with a summary line per
    strategy in the console. Compare them with
    `scripts/compare_tdx_report.py --report <dir>`.
  - Each bar then sends the same trades (date, side, shares) to `passorder`
    at the bar close, so QMT's own backtest report lists them. The backtest
    capital is set to 1e12 in `init`, so no trade is limited by cash.
  - If QMT's bars end before `C.end`, the run stops and asks for the
    history to be downloaded.
- **Live:** only on the last bar, and only when the main chart's last bar is
  today's calendar date (so never on holidays or before the day's first
  bar), and never at or after 15:00. Start the strategy once in 模型交易
  before 14:30 and leave it running; each day runs these steps:
  1. On the first tick, read the universe and the per-stock static data
     (ex-rights, total shares, contract details with today's price limits),
     and request the incremental daily download (`download_history_data`
     with an empty start) for the stocks and the indices.
  2. At `prepare_time` (14:30:00), load each stock's local daily bars up to
     yesterday, forward-adjust them with today's known ex-rights and keep
     them in memory. The data is ready when every index reaches the previous
     trading day and at most 10% of the stocks stop earlier (suspensions);
     otherwise the download is requested again.
  3. At `signal_time` (14:56:00), check readiness once more, append today's
     bar from one `get_full_tick` call per batch and compute the signals.
     If the data is still not ready, there are no new trades that day;
     pending sells still go out.
  4. At `order_time` (14:56:45), send the sells: pending sells, plus stocks
     whose buying strategy signals a sell today.
  5. At `buy_time` (14:56:50), read the account again and size the buys
     from the cash actually available. Sale proceeds count once the sells
     have filled; the broker checks available cash per order, so they are
     not counted in advance.

  All orders are limit orders `price_margin` (1.5%) through the last tick:
  buys above it, sells below it, in cents. They are kept inside the
  continuous-auction price cage (a buy at most max(2%, 0.10 yuan) above the
  best ask, a sell at most that far below the best bid) and within today's
  涨停/跌停 prices. Buys are sized at their limit price, which is the cash the broker
  freezes. While `dry_run` is on (the default), orders are only printed.
  Each step is marked as attempted before it runs, so a failure is
  reported once instead of on every tick, and a failing step is logged as
  `chao: error in <step>` without stopping the strategy. A step whose time
  has passed runs on the next tick, so after the machine sleeps or the
  client reconnects the missed steps catch up late, until 15:00.
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

## Alignment with the TDX backtest

The backtest's own trade lists reproduce the TDX exports. Below, the full
QMT backtest preparation was run on Linux with the fake client serving TDX
data, all reference stocks, 2010-01-01 to 2026-09-30, and one (current)
adjustment snapshot:

| Strategy | TDX rows | Matched | Accounting matched | Trades / wins (QMT vs TDX) | Net profit gap |
|---|---:|---:|---:|---|---:|
| 1 | 720 | 720 | 720 | 360/200 vs 360/200 | 2.32 yuan |
| 2 | 1,594 | 1,594 | 1,573 | 797/456 vs 797/456 | 0.013% |
| 3 | 1,792 | 1,790 | 1,785 | 896/521 vs 896/521 | 0.04% |
| 4 | 1,846 | 1,845 | 1,842 | 923/613 vs 923/612 | 0.01% |
| 5 | 5,370 | 5,369 | 5,352 | 2685/2110 vs 2685/2110 | 0.009% |
| 6 | 3,196 | 3,190 | 3,051 | 1597/1273 vs 1598/1274 | 0.11% |
| 7 | 600 | 598 | 590 | 300/234 vs 300/234 | 0.014% |

The remaining differences are those of PARITY.md. Exports 2 and 6 were
made from older adjustment snapshots, and QMT can only serve today's data.

QMT's own backtest engine fills the mirrored trades with its own matching.
To keep its report close, set QMT's backtest parameters as follows:
- buy commission 0.05%, sell commission 0.03%;
- no stamp duty, no minimum commission, no slippage;
- 复权方式 前复权;
- start 2010-01-01.

Two differences remain:
- QMT's forward-adjusted prices are its own;
- QMT applies T+1, so the three TDX trades bought and sold on the same bar
  (listed in the log) cannot be sold that day.

## Order rules for live trading (`chao/orders.py`)

Live trading has one account, so capital is shared. Signals and sells
follow the TDX backtest wherever a real account allows:
- **Selling:** a stock is sold when the strategy that bought it signals a
  sell. The sell then stays pending and is sent every day until the
  position is gone, as a TDX sell always completes. Only stocks chao bought
  (the ledger) are ever sold, using the sellable volume, so T+1 holds and
  manual holdings are never touched.
- **Sale proceeds:** buys go out 5 seconds after the sells and use the cash
  then available, so filled sells fund the same day's buys.
- **Position limit:** at most `max_positions` chao stocks are held at once.
- **No re-buys:** a stock already in the account is never bought again.
  Neither is a stock with a chao order today, which keeps a restarted live
  strategy from repeating an unfilled buy.
- **Buy size:** each buy targets total assets / `max_positions`, capped by
  the cash available, minus 0.2% for fees. Volumes are whole 100-share
  lots; STAR (688/689) orders need at least 200 shares.
- **Conflicts:** when several signals arrive on the same day, `priority`
  decides. Buys are taken in (priority, symbol) order, and a stock
  signalled by several strategies belongs to the highest-priority one.
- **Ledger:** the JSON file at `ledger_path`, as
  `{symbol: {"strategy": id, "selling": bool}}`. It is written after every
  placed buy and every new pending sell, and a symbol leaves it once it is
  no longer held.

## Settings

Precedence: GUI parameter > account selected in QMT (the injected `account`
variable) > `CONFIG` (from `qmt.json`) > default. QMT's parameter panel holds
numbers only, so lists and text belong in `qmt.json`.

GUI parameters are optional. To use one, add an `<item bind="<key>" .../>`
line for it to a formulaLayout xml named after the strategy, copying a
template from QMT's `python\formulaLayout` directory. No xml is generated
here, because its full schema is not documented and could not be checked.

| Key | Default | Meaning |
|---|---|---|
| `strategies` | `1,2,3,4,5,6` | strategy ids to run; 7 (Beijing) is not traded |
| `priority` | `6,5,4,3,2,1` | conflict order, highest first; must list every strategy run |
| `sectors` | `沪深A股` | QMT sectors forming the universe |
| `account_id` | the QMT account | required in backtests and for live orders |
| `dry_run` | `1` | live only: print orders instead of sending them |
| `max_positions` | `10` | most chao stocks held at once |
| `prepare_time` | `14:30:00` | live only: when to load and adjust the history up to yesterday |
| `signal_time` | `14:56:00` | live only: when to compute the day's signals |
| `order_time` | `14:56:45` | live only: when to send the sells |
| `buy_time` | `14:56:50` | live only: when to send the buys |
| `price_margin` | `0.015` | live only: limit price this far through the last price; at most 0.02, the price cage |
| `ledger_path` | (empty) | required for live orders: JSON file of chao's stocks and pending sells |
|  |  | QMT refuses file IO inside `init` ("Foribdden FileIO") but allowed the backtest's report writes to `D:\chao\report` from `handlebar`; a live run with orders writes the ledger on its first bar and stops if it cannot |
| `report_path` | (empty) | backtest: directory for the TDX-layout trade lists |
| `tdx_cash` | `1000000` | backtest: cash per (strategy, stock), as in the TDX backtest |
| `buy_fee_rate` / `sell_fee_rate` | `0.0005` / `0.0003` | backtest: TDX fee rates |

## QMT API use, checked against the official docs

Source: dict.thinktrader.net, 内置Python (innerApi): data, trading, system
functions, variable conventions and usage notes.

| Call | Documented behaviour relied on |
|---|---|
| `get_market_data_ex(fields, codes, period='1d', end_time, count, dividend_type='none', fill_data=False, subscribe=False)` | `{code: DataFrame}` indexed by `'YYYYMMDD'`; `count=-1` is every bar up to `end_time`; `subscribe=False` reads local data only, so it must be downloaded first |
| `get_divid_factors(code)` | `{epoch ms: [每股股利, 每股红股, 每股转增, 配股, 配股价, 是否股改, 除权系数]}`, amounts per share |
| `get_financial_data(['CAPITALSTRUCTURE.total_capital'], [code], start, end, report_type='announce_time')` | FINANCE(1): one stock over a range, DataFrame indexed by date, one column per field, in shares; needs 财务数据 downloaded |
| `get_instrument_detail(code)` (older clients: `get_instrumentdetail`) | `InstrumentName`; `UpStopPrice`/`DownStopPrice` are today's 涨停/跌停 prices; `get_stock_name` is slated for removal and returns GBK |
| `get_stock_list_in_sector(sector)` | list of `'600000.SH'` codes |
| `get_full_tick(codes)` | `{code: tick}` with `timetag`, `lastPrice`, `open`, `high`, `low`, `amount`; latest tick only, unusable in backtests |
| `get_bar_timetag(barpos)`, `barpos`, `period`, `do_back_test`, `start`, `end`, `is_last_bar()` | bar time in epoch ms; read-only run attributes |
| `get_trade_detail_data(account, 'STOCK', 'ACCOUNT'/'POSITION'/'ORDER', strategy)` | `m_dAvailable`, `m_dBalance`; `m_strExchangeID`, `m_strInstrumentID`, `m_nVolume`, `m_nCanUseVolume` |
| `passorder(23/24, 1101, account, code, 11, price, volume, 'chao', 2, remark, C)` | live: buy/sell by shares at a limit price; backtest uses prType 5 (latest price = bar close) with price -1; quickTrade 2 sends on the current bar, also on historical bars |

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
| Live, prepare_time: 400 bars up to yesterday, adjusted | — | bar reads + adjustment, 26 minutes before the signals | 26 bar reads |
| Live, signal_time: prepared bars plus today's tick, 6 strategies | 4.7 ms | ~24 s | 26 tick reads |
| Live, order_time: plan and send | — | under 1 s | 2–3 account reads + 1 tick read + one passorder per order |
| Backtest preparation: full history, signals plus the TDX replay | ~22 ms | ~66 s for the 3,051 reference stocks, ~2 min for 5,100 | 204 bar reads (batches of 25) + static reads |
| Backtest, per bar | — | under 1 ms | one passorder per mirrored trade |

Every phase is linear in stocks × bars.

How the original ~132 ms per stock came down to 4.7 ms:
- **Panel evaluation.** A batch of 200 stocks is one panel: one column per
  stock, rows aligned on each stock's own bars, never on dates. Each
  indicator runs once per panel instead of once per stock.
  - Rolling results are bit-identical to per-stock evaluation (checked for
    300 stocks, windows 20–250, mean/max/min).
  - Signals are identical for all 17,381 strategy-stock pairs of the
    reference stocks.
- **Shared work:** formulas are parsed once; data-only indicator calls are
  shared across strategies; comparisons and arithmetic run on numpy
  arrays.
- **Per-stock data:** qfq uses numpy plus a float fast path (identical to
  the exact algorithm on all 6,157 stocks); ex-rights are converted once a
  day; index alignment and share capital use `searchsorted`.

Why 400 bars live: the last 100 bars of a 400-bar window reproduce the
full-history signals exactly (1.74 million bars, 0 differences). A 300-bar
window differs in 8 places, because pandas' rolling sums carry rounding
from earlier bars.

Memory:
- live keeps one 200-stock batch of 400-bar frames;
- a backtest keeps the TDX trade list (tens of thousands of small
  records) plus one 25-stock batch.

Is this acceptable?
- **Live:** the bar reads and adjustment happen at 14:30; the step at
  14:56:00 reads only ticks and takes about 25 s, inside the 45 s before
  the 14:56:45 orders. QMT runs every
  strategy on one thread, so other strategies wait during that time. If
  the client's reads are slow, move `signal_time` earlier: orders still go
  out at `order_time`, or at once if the signals finish later than that.
- **Backtest:** one to two minutes of one-time preparation, then fast bars.

Known differences between live trading and the TDX backtest:
- At 14:56, today's close and AMO (turnover) are intraday values. AMO is
  still a little short of the full day, so the AMO filters (e.g.
  `AMO>200000000`) can pass less often than in a backtest that uses the
  full-day bar.
- TDX gives every (strategy, stock) its own 1,000,000; live trading shares
  one account.
- Live sells fill near 14:56:45 at the market, not at the close, and T+1
  applies.
- The backtest universe is today's sector list, so stocks delisted since
  are missing.

## Integration checklist

Run these in order; each step's log is the evidence for the next.

1. **Clock and data.**
   - Sync the Windows clock to Beijing time: every live step is timed by it.
   - In 数据管理, download daily bars for 沪深A股 and the four indices
     from 2008 on (a backtest wants about 400 days before its start), plus
     ex-rights and financial data.
2. **Startup.** Paste `qmt/chao_strategy.py` into a QMT strategy and run it
   on a daily 000001.SH chart. The first log lines show:
   - the Python, pandas and numpy versions;
   - one `probe` line per QMT data API, with its type and a sample;
   - every effective setting with its origin;
   - live only: the machine clock next to the latest tick time.

   A missing call or an unexpected shape stops the strategy at once and
   shows the value QMT returned.
3. **Short backtest.**
   - Run 2024-01-01 – 2024-06-30 with `report_path` set and QMT's backtest
     fees set as in "Alignment".
   - Check: no `warning:` lines, `backtest ready`, the `chao: tdx`
     summaries.
   - Compare:
     `scripts/compare_tdx_report.py --report <dir> --start 2024-01-01 --end 2024-06-30`.
   - Only signals are comparable in a short window. Every pair starts with
     fresh cash there, while TDX has compounded since 2010, so quantities
     and money differ.
4. **Full backtest.** 2010-01-01 – 2026-09-30, then compare without
   `--start`/`--end`. The result should match the alignment table; extra
   mismatches point to a difference between QMT's data (bars, ex-rights,
   total shares) and TDX's. With 1e12 capital, QMT's percentage returns
   mean nothing: compare trade lists and yuan profits.
5. **Live dry run** (default `dry_run`) for a few trading days:
   - Start the strategy before 14:30, so the static read (about 15,000
     calls) and the download have finished by `prepare_time`.
   - Check the `history up to <yesterday> loaded` line at 14:30: no indices
     behind, few stocks stopping earlier.
   - Check the static read time, `chao: N signals` and its time, and the
     orders marked `(dry-run)` with limit prices.
   - A dry run writes no ledger, so it shows no sells. To rehearse one, put
     a held stock in the ledger file first, e.g.
     `{"SH600000": {"strategy": 1, "selling": true}}`.
6. **Machine.** Keep it plugged in and awake from before 14:30 to 15:00:
   in Windows power options, set closing the lid and sleep to "do nothing"
   / "never", and keep Windows Update restarts outside trading hours. A
   machine asleep at 14:56 trades late, when it wakes, or not at all after
   15:00.
7. **Simulation account** with `dry_run = 0`, `ledger_path` set and a small
   `max_positions`. Check the orders in QMT, the fills, and that the ledger
   file holds the bought stocks with their strategy.
7. **Real account**, with the same settings as step 6.

On Linux, `scripts/simulate_qmt.py --start <date> --end <date>` runs step
3/4 against TDX data in QMT's shapes, through the bundled file.
