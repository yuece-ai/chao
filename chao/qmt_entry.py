"""QMT built-in strategy entry: GUI parameters -> Settings -> Context -> orders.

QMT calls init(C) once and handlebar(C) per bar; the run mode (backtest or
live) is chosen in the QMT GUI and read from C.do_back_test. The main chart
must be daily. State lives in the module-level RUN object, because QMT rolls
back attributes set on ContextInfo between handlebar calls.

- Backtest: on the first bar the signals inside the backtest window are
  computed in batches (buys as rows, sells as one boolean table per stock);
  each bar then sends that day's orders to passorder and QMT simulates them.
- Live, once a day on the last bar: per-stock static data is read on the
  first tick; at signal_time today's signals are computed (the slow part);
  at order_time the account and the latest prices are read and limit
  orders price_margin through the last price are sent, or only printed
  while dry_run is on (the default).

Settings precedence: GUI parameter > the account selected in QMT (the
injected `account` variable) > CONFIG below > field default. QMT's parameter
panel holds numbers only, so lists and text belong in CONFIG.
"""
import builtins
import datetime
import time
from collections import namedtuple
import pandas as pd
from chao.catalog import strategy_files
from chao.formulas import load_strategies
from chao.market import Context, MissingInput
from chao.orders import limit_price, plan_orders
from chao.qmt_source import ALL_BARS, HISTORY_BARS, QmtMarket, qmt_date
from chao.qmt_trade import Ledger, QmtApi, place, read_book
from chao.settings import ConfigError, Field, describe, id_list, integer, load, number, text
from chao.signals import history_signals, last_bar_signals, required_indices

# Code configuration: edit here when a value should not be a GUI parameter.
CONFIG = {}
# Stocks per batch: one get_market_data_ex call and one evaluation panel.
# Backtests read the full history, so their panels are kept smaller.
BATCH_LIVE, BATCH_BACKTEST = 200, 25
RUN = None           # the current Run; set by init()


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


def clock(value):
    """'14:56' or '14:56:45' -> '14:56:45'."""
    text = str(value).strip()
    parsed = datetime.datetime.strptime(text, '%H:%M:%S' if text.count(':') == 2 else '%H:%M')
    return parsed.strftime('%H:%M:%S')


FIELDS = [
    Field('strategies', id_list, (1, 2, 3, 4, 5, 6), 'strategy ids to run (7, Beijing, is not traded)'),
    Field('priority', id_list, (6, 5, 4, 3, 2, 1), 'conflict order, highest first; must list every strategy run'),
    Field('sectors', names, ('沪深A股',), 'QMT sectors forming the stock universe'),
    Field('account_id', text, '', 'stock account; defaults to the account selected in QMT'),
    Field('dry_run', boolean, True, 'live only: print orders instead of placing them'),
    Field('max_positions', integer, 10, 'most stocks held at once; each buy is 1/N of total assets'),
    Field('signal_time', clock, '14:56:00', 'live only: when to compute the day\'s signals (~25 s)'),
    Field('order_time', clock, '14:56:45', 'live only: when to send the orders'),
    Field('price_margin', number, 0.015, 'live only: limit price this far through the last price'),
    Field('ledger_path', text, '', 'live only: JSON file of symbols chao bought'),
]
QmtSettings = namedtuple('QmtSettings', [f.key for f in FIELDS])


class Run:
    """Per-run state kept between handlebar calls."""

    def __init__(self, context, api, ledger, backtest):
        self.context, self.api, self.ledger, self.backtest = context, api, ledger, backtest
        self.index_codes = sorted(required_indices(context.strategies.values()))
        self.buys = None              # backtest: {date: [(strategy, symbol, 'buy')]}
        self.sells = {}               # backtest: {symbol: DataFrame of sell flags, one column per strategy}
        self.prices = {}              # {(date, symbol): unadjusted close} of buy signals
        self.universe = None          # live: today's stock list
        self.static_date = None       # live: day the static data was read
        self.signals = {}             # live: {date: (buys, {symbol: [strategy]})}
        self.done = set()             # live: dates already traded


def gui_values(namespace):
    """GUI parameters are injected as globals named like the fields."""
    return {f.key: namespace[f.key] for f in FIELDS if f.key in namespace}


def qmt_account(namespace):
    """The account QMT injects when the strategy runs under 模型交易."""
    account = namespace.get('account')
    return {'account_id': account} if account else {}


def qmt_api(namespace):
    def lookup(name):
        found = namespace.get(name, getattr(builtins, name, None))
        if found is None:
            raise RuntimeError('QMT function {} is not available'.format(name))
        return found
    return QmtApi(lookup('passorder'), lookup('get_trade_detail_data'))


def check(settings, C, backtest):
    if C.period != '1d':
        raise ConfigError('the main chart must be daily (1d), not {!r}'.format(C.period))
    missing = sorted(set(settings.strategies) - set(settings.priority))
    if missing:
        raise ConfigError('priority must list every strategy run; missing {}'.format(missing))
    if not settings.account_id and (backtest or not settings.dry_run):
        raise ConfigError('account_id is required to trade or backtest')
    if settings.order_time < settings.signal_time:
        raise ConfigError('order_time must not be before signal_time')
    if not backtest and not settings.dry_run and not settings.ledger_path:
        raise ConfigError('ledger_path is required for live orders, so manual holdings are never sold')


def backtest_end(C):
    """ContextInfo.end ('%Y-%m-%d %H:%M:%S') -> 'YYYYMMDD'."""
    return qmt_date(C.end).strftime('%Y%m%d')


def build_run(C, namespace):
    loaded = load(FIELDS, [('gui', gui_values(namespace)), ('qmt account', qmt_account(namespace)),
                           ('CONFIG', CONFIG)])
    settings = QmtSettings(**loaded.values)
    backtest = bool(C.do_back_test)
    check(settings, C, backtest)
    selected = load_strategies(strategy_files())
    market = QmtMarket(C, settings.sectors, ALL_BARS if backtest else HISTORY_BARS,
                       backtest_end(C) if backtest else '')
    context = Context(settings, market, {sid: selected[sid] for sid in settings.strategies})
    ledger = Ledger('' if backtest else settings.ledger_path)
    return Run(context, qmt_api(namespace), ledger, backtest), describe(loaded)


def batches(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def record_price(run, date, symbol):
    run.prices[date, symbol] = run.context.market.unadjusted_close(symbol, date)


def report(found, errors):
    for date, sid, symbol, side in found:
        print('chao: signal {} strategy={} {} {}'.format(date, sid, symbol, side))
    for sid, symbol, message in errors:
        print('chao: skipped strategy={} {}: {}'.format(sid, symbol, message))


def trade(C, run, date, buys, sells_for, prices, placing, limits=None):
    """Plan and place (or print) one day's orders; returns them.

    buys: [(strategy, symbol, 'buy')]; sells_for(symbol) -> strategy ids with
    a sell signal today, asked only for the symbols chao holds; prices sizes
    the buys; limits: {(side, symbol): limit price} live, None in a backtest.
    """
    settings = run.context.settings
    book = read_book(run.api, settings.account_id, run.ledger.owned, live=not run.backtest)
    sells = [(sid, symbol, 'sell') for symbol in sorted(book.owned) for sid in sells_for(symbol)]
    orders = plan_orders(buys + sells, book, prices, settings.max_positions, settings.priority)
    owned = set(book.owned)
    if placing:
        run.ledger.replace(owned)  # forget symbols no longer held
    for order in orders:
        price = None if limits is None else limits.get((order.side, order.symbol))
        if limits is not None and price is None:
            print('chao: no price today for {} {}; order not sent'.format(order.side, order.symbol))
            continue
        if placing:
            place(run.api, C, settings.account_id, order, price)
            if order.side == 'buy':
                owned.add(order.symbol)
                run.ledger.replace(owned)  # at once, so a later failure cannot orphan the buy
        print('chao: order {} {} {} {} strategy={}{}{}'.format(
            date, order.side, order.symbol, order.volume, order.strategy,
            '' if price is None else ' limit={:.2f}'.format(price), '' if placing else ' (dry-run)'))
    return orders


def prepare_backtest(run, start):
    """Signals inside [start, end] for every stock, read in batches."""
    started = time.time()
    market, end = run.context.market, run.context.market.end_time
    run.buys, errors = {}, []
    for batch in batches(market.universe(), BATCH_BACKTEST):
        market.prefetch(batch, run.index_codes)
        results, bad = history_signals(run.context, batch)
        errors += bad
        for symbol, result in results.items():
            window = {sid: sig.loc[start:end] for sid, sig in result.items()}
            for sid, sig in window.items():
                for day in sig.index[sig['buy'].values]:
                    date = day.strftime('%Y-%m-%d')
                    run.buys.setdefault(date, []).append((sid, symbol, 'buy'))
                    record_price(run, date, symbol)
            sells = pd.DataFrame({sid: sig['sell'] for sid, sig in window.items()})
            if len(sells.columns) and sells.values.any():
                run.sells[symbol] = sells
    last = min(c.index[-1] for c in market.index_closes().values()).strftime('%Y%m%d')
    if last < end:
        raise MissingInput('QMT bars end on {}, before the backtest end {}; download the history first'
                           .format(last, end))
    report([], errors)
    print('chao: backtest signals ready: {} buy rows, {} stocks with sells, {} skipped, {:.0f}s'.format(
        sum(len(v) for v in run.buys.values()), len(run.sells), len(errors), time.time() - started))


def backtest_sells(run, date):
    day = pd.Timestamp(date)
    def sells_for(symbol):
        table = run.sells.get(symbol)
        if table is None or day not in table.index:
            return []
        row = table.loc[day]
        return [sid for sid in table.columns if row[sid]]
    return sells_for


def refresh_static(run, date):
    """First tick of a live day: today's universe and per-stock static data."""
    started = time.time()
    market = run.context.market
    market.reset_static()
    run.universe = market.universe()
    for symbol in run.universe:
        try:
            market.load_static(symbol)
        except MissingInput:
            pass  # reported by the signal scan, which needs the same data
    run.static_date = date
    print('chao: static data for {} stocks read in {:.0f}s'.format(len(run.universe), time.time() - started))


def live_signals(run, date):
    """Today's last-bar signals for the whole universe, read in batches."""
    started = time.time()
    market = run.context.market
    found, errors, stale = [], [], 0
    for batch in batches(run.universe, BATCH_LIVE):
        market.prefetch(batch, run.index_codes, today=date)
        behind = {code: c.index[-1] for code, c in market.index_closes().items() if c.index[-1] != pd.Timestamp(date)}
        if behind:
            raise MissingInput('indices without a bar for {}: {}'.format(date, sorted(behind)))
        stale += sum(1 for s in batch if market.last_bar_date(s) != pd.Timestamp(date))
        rows, bad = last_bar_signals(run.context, batch)
        found += [r for r in rows if r[0] == date]
        errors += bad
    report(found, errors)
    sells = {}
    for _, sid, symbol, side in found:
        if side == 'sell':
            sells.setdefault(symbol, []).append(sid)
    run.signals[date] = ([(sid, symbol, side) for _, sid, symbol, side in found if side == 'buy'], sells)
    print('chao: {} signals, {} skipped, {} without a bar today (suspended or no tick), {:.0f}s'.format(
        len(found), len(errors), stale, time.time() - started))


def live_orders(C, run, date):
    """Size and send today's orders at limit prices from the latest ticks."""
    settings, market = run.context.settings, run.context.market
    if not settings.account_id:
        print('chao: no account_id, so orders are not planned')
        return
    buys, sells = run.signals[date]
    symbols = sorted({symbol for _, symbol, _ in buys} | (set(sells) & run.ledger.owned))
    last = market.latest_prices(symbols, date)
    limits = {}
    for symbol, price in last.items():
        down, up = market.price_limits(symbol)
        for side in ('buy', 'sell'):
            limits[side, symbol] = limit_price(side, price, settings.price_margin, down, up)
    # Size buys at their limit price: that is the cash the broker freezes.
    prices = {symbol: limits.get(('buy', symbol)) for _, symbol, _ in buys}
    trade(C, run, date, buys, lambda symbol: sells.get(symbol, []), prices,
          placing=not settings.dry_run, limits=limits)


def now_hms():
    return datetime.datetime.now().strftime('%H:%M:%S')


def init(C):
    global RUN
    RUN, lines = build_run(C, globals())
    for line in lines:
        print('chao: config ' + line)
    print('chao: mode {}'.format('backtest' if RUN.backtest else 'live'))


def handlebar(C):
    run = RUN
    run.context.market.C = C  # QMT passes the ContextInfo to use for this call
    date = qmt_date(C.get_bar_timetag(C.barpos)).strftime('%Y-%m-%d')
    if run.backtest:
        if run.buys is None:
            prepare_backtest(run, qmt_date(C.start).strftime('%Y-%m-%d'))
        buys = run.buys.get(date, [])
        prices = {symbol: run.prices.get((date, symbol)) for _, symbol, _ in buys}
        trade(C, run, date, buys, backtest_sells(run, date), prices, placing=True)
        return
    if not C.is_last_bar() or date in run.done:
        return
    if run.static_date != date:
        refresh_static(run, date)
    now = now_hms()
    settings = run.context.settings
    if date not in run.signals and now >= settings.signal_time:
        run.signals[date] = ([], {})  # a failure below is reported once, not on every tick
        live_signals(run, date)
    if date in run.signals and now >= settings.order_time:
        run.done.add(date)
        live_orders(C, run, date)
