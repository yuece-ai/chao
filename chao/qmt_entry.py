"""QMT built-in strategy entry: GUI parameters -> Settings -> Context -> orders.

QMT injects the strategy's GUI parameters as module globals, calls init(C)
once and handlebar(C) per bar. Backtest or live is chosen in the QMT GUI
and read from C.do_back_test:
- backtest: every bar's signals are computed once, then each bar's orders
  are passed to passorder and QMT simulates the fills;
- live: on the last bar, once a day at trade_time, today's orders are
  placed, or only printed while dry_run is on (the default).

Precedence: GUI parameter > CONFIG below > field default.
"""
import builtins
import datetime
from collections import namedtuple
from chao.catalog import strategy_files
from chao.formulas import load_strategies
from chao.market import Context
from chao.orders import plan_orders
from chao.qmt_source import ALL_BARS, HISTORY_BARS, QmtMarket, qmt_date
from chao.qmt_trade import QUICK_BACKTEST, QUICK_LIVE, Ledger, QmtApi, place, read_book
from chao.settings import ConfigError, Field, describe, id_list, integer, load, text
from chao.signals import scan

# Code configuration: edit here when a value should not be a GUI parameter.
CONFIG = {}


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
    """'14:50' -> '14:50'."""
    parsed = datetime.datetime.strptime(str(value).strip(), '%H:%M')
    return parsed.strftime('%H:%M')


FIELDS = [
    Field('strategies', id_list, (1, 2, 3, 4, 5, 6), 'strategy ids to run (7, Beijing, is not traded)'),
    Field('priority', id_list, (6, 5, 4, 3, 2, 1), 'conflict order, highest first; must list every strategy run'),
    Field('sectors', names, ('沪深A股',), 'QMT sectors forming the stock universe'),
    Field('account_id', text, '', 'stock account for orders; any name in a backtest, e.g. testS'),
    Field('dry_run', boolean, True, 'live only: print orders instead of placing them'),
    Field('max_positions', integer, 10, 'most stocks held at once; each buy is 1/N of total assets'),
    Field('trade_time', clock, '14:50', 'live only: earliest time of day to trade'),
    Field('ledger_path', text, '', 'live only: JSON file of symbols chao bought'),
]
QmtSettings = namedtuple('QmtSettings', [f.key for f in FIELDS])


class Run:
    """Per-run state kept on ContextInfo between handlebar calls."""

    def __init__(self, context, api, ledger, backtest):
        self.context, self.api, self.ledger, self.backtest = context, api, ledger, backtest
        self.signals_by_date = None   # backtest: {date: [(strategy, symbol, side)]}
        self.done = set()             # live: dates already traded


def gui_values(namespace):
    """GUI parameters are injected as globals named like the fields."""
    return {f.key: namespace[f.key] for f in FIELDS if f.key in namespace}


def qmt_api(namespace):
    def lookup(name):
        found = namespace.get(name, getattr(builtins, name, None))
        if found is None:
            raise RuntimeError('QMT function {} is not available'.format(name))
        return found
    return QmtApi(lookup('passorder'), lookup('get_trade_detail_data'))


def check(settings, backtest):
    missing = sorted(set(settings.strategies) - set(settings.priority))
    if missing:
        raise ConfigError('priority must list every strategy run; missing {}'.format(missing))
    if not settings.account_id and (backtest or not settings.dry_run):
        raise ConfigError('account_id is required to trade or backtest')
    if not backtest and not settings.dry_run and not settings.ledger_path:
        raise ConfigError('ledger_path is required for live orders, so manual holdings are never sold')


def build_run(C, namespace):
    loaded = load(FIELDS, [('gui', gui_values(namespace)), ('CONFIG', CONFIG)])
    settings = QmtSettings(**loaded.values)
    backtest = bool(C.do_back_test)
    check(settings, backtest)
    selected = load_strategies(strategy_files())
    market = QmtMarket(C, settings.sectors, ALL_BARS if backtest else HISTORY_BARS)
    context = Context(settings, market, {sid: selected[sid] for sid in settings.strategies})
    ledger = Ledger('' if backtest else settings.ledger_path)
    return Run(context, qmt_api(namespace), ledger, backtest), describe(loaded)


def report(found, errors):
    for date, sid, symbol, side in found:
        print('chao: signal {} strategy={} {} {}'.format(date, sid, symbol, side))
    for sid, symbol, message in errors:
        print('chao: skipped strategy={} {}: {}'.format(sid, symbol, message))


def trade(C, run, date, signals, placing):
    """Plan and place (or print) one day's orders; returns them."""
    settings = run.context.settings
    book = read_book(run.api, settings.account_id, run.ledger.owned)
    buys = {symbol for _, symbol, side in signals if side == 'buy'}
    prices = {s: run.context.market.unadjusted_close(s, date) for s in buys}
    orders = plan_orders(signals, book, prices, settings.max_positions, settings.priority)
    quick = QUICK_BACKTEST if run.backtest else QUICK_LIVE
    for order in orders:
        if placing:
            place(run.api, C, settings.account_id, order, quick)
        print('chao: order {} {} {} {} strategy={}{}'.format(
            date, order.side, order.symbol, order.volume, order.strategy, '' if placing else ' (dry-run)'))
    if placing:
        run.ledger.replace(book.owned | {o.symbol for o in orders if o.side == 'buy'})
    return orders


def init(C):
    C.chao, lines = build_run(C, globals())
    for line in lines:
        print('chao: config ' + line)
    print('chao: mode {}'.format('backtest' if C.chao.backtest else 'live'))


def handlebar(C):
    run = C.chao
    date = qmt_date(C.get_bar_timetag(C.barpos)).strftime('%Y-%m-%d')
    if run.backtest:
        if run.signals_by_date is None:
            found, errors = scan(run.context, last_only=False)
            report([], errors)
            run.signals_by_date = {}
            for day, sid, symbol, side in found:
                run.signals_by_date.setdefault(day, []).append((sid, symbol, side))
            print('chao: backtest signals ready: {} rows, {} skipped'.format(len(found), len(errors)))
        trade(C, run, date, run.signals_by_date.get(date, []), placing=True)
        return
    if not C.is_last_bar() or date in run.done:
        return
    if datetime.datetime.now().strftime('%H:%M') < run.context.settings.trade_time:
        return
    found, errors = scan(run.context, last_only=True)
    found = [row for row in found if row[0] == date]
    report(found, errors)
    if run.context.settings.account_id:
        trade(C, run, date, [(sid, symbol, side) for _, sid, symbol, side in found],
              placing=not run.context.settings.dry_run)
    else:
        print('chao: no account_id, so orders are not planned')
    run.done.add(date)
    print('chao: {} signals, {} skipped'.format(len(found), len(errors)))
