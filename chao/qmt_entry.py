"""QMT built-in strategy entry: GUI parameters -> Settings -> Context -> orders.

QMT calls init(C) once and handlebar(C) per bar. The run mode is decided on
the first handlebar call, not in init: in the client, C.do_back_test was still
False in init for a backtest. `mode` 'auto' follows C.do_back_test there;
'backtest' or 'live' forces it. The main chart must be daily. State lives in the module-level RUN object, because QMT rolls
back attributes set on ContextInfo between handlebar calls.

- Backtest, aligned with the user's TDX backtest: on the first bar every
  (strategy, stock) pair is replayed with the TDX ledger (its own
  tdx_cash, fills at the close, TDX fees, float32 money). The trades are
  written in the TDX export layout to report_path, and each bar sends the
  same trades (date, side, shares) to passorder, so QMT's own backtest
  report shows them too.
- Live, on the last bar of a day whose bar is today's calendar date. The
  strategy stays running in 模型交易; each day it steps through:
  first tick: request the missing daily bars (incremental, non-blocking)
              and read per-stock static data;
  prepare_time (14:30): check the local bars reach the previous trading
              day, then read and adjust every stock's history once;
  signal_time (14:56): append today's bar from the latest ticks and
              compute the signals;
  order_time: send the pending sells; buy_time: send the buys, sized from
              the cash then available.
  When the data is not ready by signal_time, no new trade is made that day;
  sells already pending still go out. A failing step is logged and never
  stops the strategy. Orders are limit orders price_margin through the last
  price, or only printed while dry_run is on (the default). Nothing is sent
  from MARKET_CLOSE on.

Settings precedence: GUI parameter > the account selected in QMT (the
injected `account` variable) > CONFIG below > field default. QMT's parameter
panel holds numbers only, so lists and text belong in CONFIG.
"""
import builtins
import datetime
import os
import sys
import time
from collections import namedtuple
import pandas as pd
from chao.catalog import strategy_files
from chao.formulas import load_strategies
from chao.market import Context, MissingInput
from chao.orders import CAGE_RATE, Order, limit_price, plan_orders
from chao.qmt_source import HISTORY_BARS, QmtMarket, probe, qmt_date
from chao.qmt_trade import Ledger, QmtApi, place, read_book
from chao.settings import ConfigError, Field, describe, id_list, integer, load, number, text
from chao.replay import BUY, ReplaySpec, replay
from chao.signals import last_bar_signals, panel_signals, required_indices
from chao.tdx_report import summary_line, write_exports

# Code configuration: edit here when a value should not be a GUI parameter.
CONFIG = {}
# Stocks per batch: one get_market_data_ex call and one evaluation panel.
# Backtests read the full history, so their panels are kept smaller.
BATCH_LIVE, BATCH_BACKTEST = 200, 25
# QMT backtest capital: large enough that every TDX trade, each sized from
# its own tdx_cash, can be filled at once in one account.
BACKTEST_CAPITAL = 1e12
MARKET_CLOSE = '15:00:00'
# Live readiness: at most this share of stocks may lack the previous trading
# day's bar (suspensions); more means the local download is incomplete.
STALE_LIMIT = 0.10
# Days of history wanted before a backtest's start: MA(250) plus REF lookback.
WARMUP_DAYS = 400
# Backtest bars read before the range: indicators over a window of at least
# 400 bars equal full-history ones on its later bars (0 differences in 1.74
# million bars; 300 differed in 8), so a stock's bars from the range start on
# match a full-history read.
WARMUP_BARS = 500
# First trading day of each formula index: an index cannot have older bars.
INDEX_SINCE = {'999999': '1990-12-19', '399001': '1991-04-03', '399006': '2010-06-01',
               '000688': '2019-12-31', '899050': '2022-04-29'}
RUN = None           # the current Run; set on the first handlebar call
NAMESPACE = None     # the strategy's globals, saved by init for that call


def names(value):
    """'沪深A股' or a comma list -> tuple of names."""
    items = value.split(',') if isinstance(value, str) else value
    return tuple(str(x).strip() for x in items if str(x).strip())


def boolean(value):
    """GUI 0/1 or 'true'/'false'."""
    if isinstance(value, str):
        if value.strip().lower() not in ('0', '1', 'true', 'false'):
            raise ValueError('expected 0/1/true/false')
        return value.strip().lower() in ('1', 'true')
    return bool(value)


def run_mode(value):
    text = str(value).strip().lower()
    if text not in ('auto', 'backtest', 'live'):
        raise ValueError("expected 'auto', 'backtest' or 'live'")
    return text


def clock(value):
    """'14:56' or '14:56:45' -> '14:56:45'."""
    text = str(value).strip()
    parsed = datetime.datetime.strptime(text, '%H:%M:%S' if text.count(':') == 2 else '%H:%M')
    return parsed.strftime('%H:%M:%S')


FIELDS = [
    Field('mode', run_mode, 'auto', "'auto': QMT's do_back_test at the first bar; 'backtest' or 'live' forces it"),
    Field('strategies', id_list, (1, 2, 3, 4, 5, 6), 'strategy ids to run (7, Beijing, is not traded)'),
    Field('priority', id_list, (6, 5, 4, 3, 2, 1), 'conflict order, highest first; must list every strategy run'),
    Field('sectors', names, ('沪深A股',), 'QMT sectors forming the stock universe'),
    Field('account_id', text, '', 'stock account; defaults to the account selected in QMT'),
    Field('dry_run', boolean, True, 'live only: print orders instead of placing them'),
    Field('max_positions', integer, 10, 'most stocks held at once; each buy is 1/N of total assets'),
    Field('prepare_time', clock, '14:30:00', 'live only: when to check and load the history up to yesterday'),
    Field('signal_time', clock, '14:56:00', 'live only: when to append today\'s bar and compute the signals'),
    Field('order_time', clock, '14:56:45', 'live only: when to send the sells'),
    Field('buy_time', clock, '14:56:50', 'live only: when to send the buys, after the sells have filled'),
    Field('price_margin', number, 0.015, 'live only: limit price this far through the last price'),
    Field('ledger_path', text, '', 'live only: JSON file of the stocks chao bought and their pending sells'),
    Field('report_path', text, '', 'backtest only: directory for the TDX-layout trade lists'),
    Field('tdx_cash', number, 1000000.0, 'backtest: cash per (strategy, stock), as in the TDX backtest'),
    Field('buy_fee_rate', number, 0.0005, 'backtest: TDX buy fee rate'),
    Field('sell_fee_rate', number, 0.0003, 'backtest: TDX sell fee rate'),
]
QmtSettings = namedtuple('QmtSettings', [f.key for f in FIELDS])


class Run:
    """Per-run state kept between handlebar calls."""

    def __init__(self, context, api, ledger, backtest):
        self.context, self.api, self.ledger, self.backtest = context, api, ledger, backtest
        self.index_codes = sorted(required_indices(context.strategies.values()))
        self.trades = None            # backtest: {date: [Order]} from the TDX replay
        self.universe = None          # live: today's stock list
        self.static_date = None       # live: day the static data was read and the download requested
        self.prepared = {}            # live: {date: True if the history was ready}
        self.download = None          # live: QMT's download_history_data
        self.signals = {}             # live: {date: (buys, {symbol: [strategy]})}
        self.sold = set()             # live: dates whose sells were sent
        self.done = set()             # live: dates fully traded
        self.waiting = None           # live: last day reported as not tradable
        self.failed = False           # backtest: preparation failed; stop


def gui_values(namespace):
    """GUI parameters are injected as globals named like the fields."""
    return {f.key: namespace[f.key] for f in FIELDS if f.key in namespace}


def qmt_account(namespace):
    """The account QMT injects when the strategy runs under 模型交易."""
    account = namespace.get('account')
    return {'account_id': account} if account else {}


def qmt_function(namespace, name):
    found = namespace.get(name, getattr(builtins, name, None))
    if found is None:
        raise RuntimeError('QMT function {} is not available'.format(name))
    return found


def qmt_api(namespace):
    return QmtApi(qmt_function(namespace, 'passorder'), qmt_function(namespace, 'get_trade_detail_data'))


def check(settings, C, backtest):
    if C.period != '1d':
        raise ConfigError('the main chart must be daily (1d), not {!r}'.format(C.period))
    missing = sorted(set(settings.strategies) - set(settings.priority))
    if missing:
        raise ConfigError('priority must list every strategy run; missing {}'.format(missing))
    if not settings.account_id and (backtest or not settings.dry_run):
        raise ConfigError('account_id is required to trade or backtest')
    if not 0 < settings.price_margin <= CAGE_RATE:
        raise ConfigError('price_margin must be in (0, {}]: the price cage rejects orders beyond it'.format(CAGE_RATE))
    if not settings.prepare_time <= settings.signal_time <= settings.order_time <= settings.buy_time < MARKET_CLOSE:
        raise ConfigError('need prepare_time <= signal_time <= order_time <= buy_time < {}'.format(MARKET_CLOSE))
    if not backtest and not settings.dry_run and not settings.ledger_path:
        raise ConfigError('ledger_path is required for live orders, so manual holdings are never sold')


def backtest_end(C):
    """ContextInfo.end ('%Y-%m-%d %H:%M:%S') -> 'YYYYMMDD'."""
    return qmt_date(C.end).strftime('%Y%m%d')


def load_settings(namespace):
    loaded = load(FIELDS, [('gui', gui_values(namespace)), ('qmt account', qmt_account(namespace)),
                           ('CONFIG', CONFIG)])
    return QmtSettings(**loaded.values), describe(loaded)


def is_backtest(settings, C):
    return settings.mode == 'backtest' or (settings.mode == 'auto' and bool(C.do_back_test))


def build_run(C, namespace):
    settings, lines = load_settings(namespace)
    backtest = is_backtest(settings, C)
    check(settings, C, backtest)
    selected = load_strategies(strategy_files())
    last_bar = qmt_date(C.get_bar_timetag(C.time_tick_size - 1))  # the data's last day
    market = QmtMarket(C, settings.sectors, backtest_bars(C) if backtest else HISTORY_BARS,
                       backtest_end(C) if backtest else '', last_bar)
    context = Context(settings, market, {sid: selected[sid] for sid in settings.strategies})
    ledger = Ledger('' if backtest else settings.ledger_path)
    if not backtest and not settings.dry_run:
        ledger.save()  # QMT's sandbox forbids most folders: fail now, not after the first buy
    run = Run(context, qmt_api(namespace), ledger, backtest)
    if not backtest:
        run.download = qmt_function(namespace, 'download_history_data')
    return run, lines


def batches(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def report(found, errors):
    for date, sid, symbol, side in found:
        print('chao: signal {} strategy={} {} {}'.format(date, sid, symbol, side))
    for sid, symbol, message in errors:
        print('chao: skipped strategy={} {}: {}'.format(sid, symbol, message))


def send(C, run, date, orders, limits, placing):
    """Send (or print) planned live orders at their limit prices."""
    settings, ledger = run.context.settings, run.ledger
    for order in orders:
        price = limits.get((order.side, order.symbol))
        if price is None:
            print('chao: no price today for {} {}; order not sent'.format(order.side, order.symbol))
            continue
        if placing:
            place(run.api, C, settings.account_id, order, price)
            if order.side == 'buy':
                ledger.bought(order.symbol, order.strategy)  # at once, so a later failure cannot orphan it
        print('chao: order {} {} {} {} strategy={} limit={:.2f}{}'.format(
            date, order.side, order.symbol, order.volume, order.strategy, price, '' if placing else ' (dry-run)'))


def limits_for(run, symbols, date):
    """{(side, symbol): limit price} from the latest ticks and today's limits."""
    settings, market = run.context.settings, run.context.market
    limits = {}
    for symbol, quote in market.latest_quotes(sorted(symbols), date).items():
        down, up = market.price_limits(symbol)
        for side in ('buy', 'sell'):
            limits[side, symbol] = limit_price(side, quote.last, settings.price_margin, down, up,
                                               ask=quote.ask, bid=quote.bid)
    return limits


def live_sells(C, run, date):
    """A held stock is sold when the strategy that bought it signals a sell,
    and stays pending until it is gone, as in the TDX backtest."""
    settings, ledger = run.context.settings, run.ledger
    placing = not settings.dry_run
    _, sells_today = run.signals[date]
    book = read_book(run.api, settings.account_id, ledger.owned, live=True)
    if placing:
        ledger.keep(book.owned)
    pending = sorted(s for s in book.owned
                     if ledger.selling(s) or ledger.owner(s) in sells_today.get(s, []))
    if placing:
        for symbol in pending:
            ledger.sell(symbol)
    orders = plan_orders([(ledger.owner(s), s, 'sell') for s in pending], book, {},
                         settings.max_positions, settings.priority)
    send(C, run, date, orders, limits_for(run, pending, date), placing)


def live_buys(C, run, date):
    """Buys sized from the cash available now; the broker freezes the limit
    price, so that is the price used for sizing."""
    settings, ledger = run.context.settings, run.ledger
    buys, _ = run.signals[date]
    book = read_book(run.api, settings.account_id, ledger.owned, live=True)
    limits = limits_for(run, {symbol for _, symbol, _ in buys}, date)
    prices = {symbol: limits.get(('buy', symbol)) for _, symbol, _ in buys}
    orders = plan_orders(buys, book, prices, settings.max_positions, settings.priority)
    send(C, run, date, orders, limits, not settings.dry_run)


def say(line):
    """print, flushed at once: a long step stays visibly alive in QMT's log."""
    print(line)
    sys.stdout.flush()


def last_chart_position(C, end):
    """Position of the main chart's last bar on or before end ('YYYY-MM-DD');
    the end may fall on a weekend or holiday (2024-06-30 was a Sunday)."""
    low, high = 0, C.time_tick_size - 1
    while low < high:
        middle = (low + high + 1) // 2
        if qmt_date(C.get_bar_timetag(middle)).strftime('%Y-%m-%d') <= end:
            low = middle
        else:
            high = middle - 1
    return low


def last_chart_day(C, end):
    return qmt_date(C.get_bar_timetag(last_chart_position(C, end))).strftime('%Y-%m-%d')


def backtest_bars(C):
    """Bars per stock a backtest reads: its own range on the main chart plus
    WARMUP_BARS before it, not the whole history since 1990. Called on the
    first bar, where C.barpos is the range's first bar."""
    end = qmt_date(C.end).strftime('%Y-%m-%d')
    return last_chart_position(C, end) - C.barpos + 1 + WARMUP_BARS


def prepare_backtest(run, start, end, last_day):
    """Replay every (strategy, stock) pair with the TDX ledger over
    [start, end], write the TDX-layout trade lists and queue the trades.
    The index bars must reach last_day, the chart's last trading day."""
    started = time.time()
    settings, market = run.context.settings, run.context.market
    spec = ReplaySpec(start, end, settings.tdx_cash, settings.buy_fee_rate, settings.sell_fee_rate)
    strategies = [s for _, s in sorted(run.context.strategies.items())]
    events = {sid: [] for sid in run.context.strategies}
    summaries = {sid: [] for sid in run.context.strategies}
    stock_names, errors, trades = {}, [], {}
    universe = market.universe()
    groups = batches(universe, BATCH_BACKTEST)
    say('chao: backtest preparing {} stocks {} .. {}, in {} batches; progress every 10%'.format(
        len(universe), start, end, len(groups)))
    for number, batch in enumerate(groups, 1):
        if number * 10 // len(groups) != (number - 1) * 10 // len(groups):
            say('chao: backtest preparing {}/{} stocks, {:.0f}s'.format(
                min(number * BATCH_BACKTEST, len(universe)), len(universe), time.time() - started))
        market.prefetch(batch, run.index_codes)
        if number == 1:  # fail before the long replay, not after it
            last = min(c.index[-1] for c in market.index_closes().values()).strftime('%Y-%m-%d')
            if last < last_day:
                raise MissingInput('QMT index bars end on {}, before the last trading day {} of the backtest; '
                                   'download the history first'.format(last, last_day))
        bars, results, bad = panel_signals(strategies, market, batch)
        errors += bad
        for symbol, by_strategy in results.items():
            stock_names[symbol[2:]] = market.name(symbol)
            for sid, sig in by_strategy.items():
                rows, summary = replay(bars[symbol], sig, spec)
                summaries[sid].append(summary)
                for e in rows:
                    e.update(strategy=sid, code=symbol[2:])
                    side = 'buy' if e['direction'] == BUY else 'sell'
                    trades.setdefault(e['date'], []).append(Order(side, symbol, e['quantity'], sid))
                events[sid] += rows
    ex_rights, shares = market.latest_static_dates()
    for what, latest in (('ex-rights', ex_rights), ('share-capital change', shares)):
        if latest is not None and latest < pd.Timestamp(start):
            print('chao: warning: the latest {} QMT returned is {}, before the backtest start; QMT may cut '
                  'static data at the current bar, which would make qfq or FINANCE wrong'.format(what, latest.date()))
    wanted = pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)
    for code, close in sorted(market.index_closes().items()):
        since = max(wanted, pd.Timestamp(INDEX_SINCE[code]))
        if close.index[0] > since + pd.Timedelta(days=10):
            print('chao: warning: index {} history starts {}; download bars from {} on, or signals near the '
                  'start differ from TDX'.format(code, close.index[0].date(), since.date()))
    report([], errors)
    for sid in sorted(summaries):
        print('chao: tdx ' + summary_line(sid, summaries[sid]))
    if settings.report_path:
        try:
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            for path in write_exports(settings.report_path, events, stock_names, stamp):
                say('chao: wrote ' + path)
        except OSError as exc:  # the trade lists are a copy for comparison; the backtest itself goes on
            say('chao: warning: cannot write the trade lists to {}: {}: {}'.format(
                settings.report_path, type(exc).__name__, exc))
    same_day = sorted({(o.symbol, d) for d, orders in trades.items() for o in orders if o.side == 'sell'
                       and any(b.side == 'buy' and b.symbol == o.symbol and b.strategy == o.strategy for b in orders)})
    for symbol, day in same_day:
        print('chao: {} {} is bought and sold on the same bar in TDX; QMT applies T+1 and cannot sell it that day'
              .format(day, symbol))
    run.trades = trades  # only a complete preparation is used
    say('chao: backtest ready: {} trades, {} skipped, {:.0f}s'.format(
        sum(len(v) for v in events.values()), len(errors), time.time() - started))


def backtest_bar(C, run, date):
    """Send the TDX trades of this bar to QMT's backtest engine."""
    account = run.context.settings.account_id
    for order in run.trades.get(date, []):
        place(run.api, C, account, order)
        print('chao: order {} {} {} {} strategy={}'.format(date, order.side, order.symbol, order.volume,
                                                           order.strategy))


def refresh_static(run, date):
    """First tick of a live day: today's universe, per-stock static data, and
    a request for the daily bars still missing locally."""
    started = time.time()
    market = run.context.market
    run.static_date = date  # before reading: a failure is reported once, not on every tick
    market.reset_static()
    run.universe = market.universe()
    for symbol in run.universe:
        try:
            market.load_static(symbol)
        except MissingInput:
            pass  # reported by the signal scan, which needs the same data
    market.download_daily(run.universe, run.index_codes, run.download)
    print('chao: static data for {} stocks read and daily bars requested in {:.0f}s'.format(
        len(run.universe), time.time() - started))


def prepare_day(C, run, date):
    """Load the history up to yesterday once; ready when the indices and all
    but STALE_LIMIT of the stocks reach the previous trading day."""
    started = time.time()
    market = run.context.market
    previous = qmt_date(C.get_bar_timetag(C.barpos - 1))
    last = market.prepare(run.universe, run.index_codes, date, BATCH_LIVE)
    behind_indices = sorted(code for code in run.index_codes if last.get(code) != previous)
    stocks = [last[s] for s in run.universe if s in last]
    behind_stocks = sum(1 for d in stocks if d < previous)
    ready = not behind_indices and behind_stocks <= STALE_LIMIT * len(stocks)
    run.prepared[date] = ready
    print('chao: history up to {} loaded for {} stocks in {:.0f}s; {} stop earlier, {} have no local bars; '
          'indices behind: {}'.format(previous.date(), len(stocks), time.time() - started, behind_stocks,
                                      len(run.universe) - len(stocks), behind_indices or 'none'))
    if not ready:
        print('chao: warning: local daily bars are incomplete; downloading again and checking at signal_time')
        market.download_daily(run.universe, run.index_codes, run.download)


def live_signals(run, date):
    """Today's last-bar signals: prepared history plus today's ticks."""
    started = time.time()
    market = run.context.market
    found, errors, without_tick = [], [], 0
    market.bad_amount = []
    for batch in batches(run.universe, BATCH_LIVE):
        market.load_today(batch, date)
        behind = sorted(code for code, c in market.index_closes().items() if c.index[-1] != pd.Timestamp(date))
        if behind:
            raise MissingInput('indices without a tick for {}: {}'.format(date, behind))
        without_tick += sum(1 for s in batch if market.last_bar_date(s) != pd.Timestamp(date))
        rows, bad = last_bar_signals(run.context, batch)
        found += [r for r in rows if r[0] == date]
        errors += bad
    report(found, errors)
    sells = {}
    for _, sid, symbol, side in found:
        if side == 'sell':
            sells.setdefault(symbol, []).append(sid)
    run.signals[date] = ([(sid, symbol, side) for _, sid, symbol, side in found if side == 'buy'], sells)
    print('chao: {} signals, {} skipped, {} without a tick today (suspended), {:.0f}s'.format(
        len(found), len(errors), without_tick, time.time() - started))
    if market.bad_amount:
        print('chao: warning: {} ticks have an amount in no known unit, so no bar today: {}'.format(
            len(market.bad_amount), ' '.join(market.bad_amount[:20])))


def guarded(step, *args):
    """Run one live step; log a failure instead of stopping the strategy."""
    try:
        step(*args)
        return True
    except Exception as exc:  # the strategy must survive to trade pending sells and later days
        print('chao: error in {}: {}: {}'.format(step.__name__, type(exc).__name__, exc))
        return False


def now():
    return datetime.datetime.now()


def init(C):
    init_with(C, globals())


REQUIRED_CALLS = ['get_market_data_ex', 'get_divid_factors', 'get_financial_data', 'get_stock_list_in_sector',
                  'get_bar_timetag', 'get_full_tick', 'is_last_bar']


def check_environment(C, namespace):
    """Print the client environment and fail fast on a missing API."""
    import numpy as np
    print('chao: python {} pandas {} numpy {}'.format(sys.version.split()[0], pd.__version__, np.__version__))
    missing = [m for m in REQUIRED_CALLS if not callable(getattr(C, m, None))]
    if not (callable(getattr(C, 'get_instrument_detail', None)) or callable(getattr(C, 'get_instrumentdetail', None))):
        missing.append('get_instrument_detail')
    missing += [g for g in ('passorder', 'get_trade_detail_data')
                if not callable(namespace.get(g, getattr(builtins, g, None)))]
    if missing:
        raise ConfigError('QMT API not available: {}'.format(missing))


def init_with(C, namespace):
    global RUN, NAMESPACE
    check_environment(C, namespace)
    settings, _ = load_settings(namespace)
    RUN, NAMESPACE = None, namespace
    print('chao: init do_back_test={} mode={}'.format(getattr(C, 'do_back_test', None), settings.mode))
    if is_backtest(settings, C):
        C.capital = BACKTEST_CAPITAL  # capital can only be set in init; every TDX trade has its own tdx_cash


def first_bar(C):
    """First handlebar call: decide the mode, build the run, probe the APIs."""
    global RUN
    run, lines = build_run(C, NAMESPACE)
    for line in lines:
        print('chao: config ' + line)
    print('chao: mode {} (do_back_test={} at the first bar)'.format(
        'backtest' if run.backtest else 'live', getattr(C, 'do_back_test', None)))
    if run.backtest and getattr(C, 'capital', None) != BACKTEST_CAPITAL:
        print("chao: warning: the backtest capital was not raised in init; set it high in QMT's backtest "
              "settings, or set mode='backtest', so no mirrored trade is short of cash")
    market = run.context.market
    for line in probe(C, market.sectors, market.end_time, not run.backtest, now().strftime('%Y-%m-%d %H:%M:%S')):
        print('chao: ' + line)
    RUN = run


def handlebar(C):
    if RUN is None:
        first_bar(C)
    run = RUN
    if run.failed:
        return
    run.context.market.C = C  # QMT passes the ContextInfo to use for this call
    if run.backtest:
        backtest_tick(C, run)
    else:
        live_tick(C, run)


def backtest_tick(C, run):
    if run.trades is None:
        run.failed = True  # until the preparation completes: never trade on a partial list
        start, end = qmt_date(C.start).strftime('%Y-%m-%d'), qmt_date(C.end).strftime('%Y-%m-%d')
        prepare_backtest(run, start, end, last_chart_day(C, end))
        run.failed = False
    backtest_bar(C, run, qmt_date(C.get_bar_timetag(C.barpos)).strftime('%Y-%m-%d'))


def live_tick(C, run):
    if not C.is_last_bar():
        return
    moment = now()
    today, clock_now = moment.strftime('%Y-%m-%d'), moment.strftime('%H:%M:%S')
    if today in run.done:
        return
    bar_day = qmt_date(C.get_bar_timetag(C.barpos)).strftime('%Y-%m-%d')
    if bar_day != today:
        if run.waiting != today:  # a holiday, or before the day's first bar
            print('chao: the main chart has no bar for {} (last {}); not trading yet'.format(today, bar_day))
            run.waiting = today
        return
    settings = run.context.settings
    if clock_now >= MARKET_CLOSE:
        run.done.add(today)
        print('chao: {} started after {}; no orders today'.format(today, MARKET_CLOSE))
        return
    if run.static_date != today:
        guarded(refresh_static, run, today)
    if today not in run.prepared and clock_now >= settings.prepare_time:
        run.prepared[today] = False  # marked first: a failure is reported once
        guarded(prepare_day, C, run, today)
    if today not in run.signals and clock_now >= settings.signal_time:
        run.signals[today] = ([], {})  # no new trades unless the signals below succeed
        if not run.prepared.get(today):
            guarded(prepare_day, C, run, today)  # second and last check
        if run.prepared.get(today):
            guarded(live_signals, run, today)
        else:
            print('chao: data not ready for {}; no new trades today, pending sells still go out'.format(today))
    if not settings.account_id:
        if today in run.signals:
            run.done.add(today)
            print('chao: no account_id, so orders are not planned')
        return
    if today in run.signals and today not in run.sold and clock_now >= settings.order_time:
        run.sold.add(today)
        guarded(live_sells, C, run, today)
    if today in run.sold and clock_now >= settings.buy_time:
        run.done.add(today)
        guarded(live_buys, C, run, today)
