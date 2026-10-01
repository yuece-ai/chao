"""QMT built-in strategy entry: GUI parameters -> Settings -> Context -> signals.

QMT injects the strategy's GUI parameters (formulaLayout xml) as module
globals, calls init(C) once and handlebar(C) on every bar. On the last bar
the day's signals are computed and printed. No orders are placed.

Precedence: GUI parameter > CONFIG below > field default.
"""
from collections import namedtuple
from chao.catalog import strategy_files
from chao.formulas import load_strategies
from chao.market import Context, MissingInput
from chao.qmt_source import QmtMarket
from chao.settings import ConfigError, Field, describe, id_list, load
from chao.signals import stock_signals, strategy_universe

# Code configuration: edit here when a value should not be a GUI parameter.
CONFIG = {}


def names(value):
    """'沪深A股,京市A股' or a list -> tuple of names."""
    items = value.split(',') if isinstance(value, str) else value
    return tuple(str(x).strip() for x in items if str(x).strip())


FIELDS = [
    Field('strategies', id_list, (1, 2, 3, 4, 5, 6, 7), 'strategy ids to run'),
    Field('sectors', names, ('沪深A股', '京市A股'), 'QMT sectors forming the stock universe'),
]
QmtSettings = namedtuple('QmtSettings', [f.key for f in FIELDS])


def gui_values(namespace):
    """GUI parameters are injected as globals named like the fields."""
    return {f.key: namespace[f.key] for f in FIELDS if f.key in namespace}


def build_context(context_info, gui):
    loaded = load(FIELDS, [('gui', gui), ('CONFIG', CONFIG)])
    settings = QmtSettings(**loaded.values)
    selected = load_strategies(strategy_files())
    strategies = {sid: selected[sid] for sid in settings.strategies}
    return Context(settings, QmtMarket(context_info, settings.sectors), strategies), describe(loaded)


def day_signals(context):
    """[(strategy, symbol, 'buy'|'sell', date)] on the last bar, and per-stock errors."""
    symbols = context.market.universe()
    found, errors = [], []
    for sid, strategy in sorted(context.strategies.items()):
        for symbol in strategy_universe(sid, symbols):
            try:
                frame, sig = stock_signals(strategy, context.market, symbol)
            except (MissingInput, KeyError, ValueError) as exc:
                errors.append((sid, symbol, '{}: {}'.format(type(exc).__name__, exc)))
                continue
            if sig is None:
                continue
            last = sig.iloc[-1]
            date = sig.index[-1].strftime('%Y-%m-%d')
            for side in ('buy', 'sell'):
                if bool(last[side]):
                    found.append((sid, symbol, side, date))
    return found, errors


def init(C):
    try:
        C.chao, lines = build_context(C, gui_values(globals()))
    except ConfigError as exc:
        print('chao: config error: {}'.format(exc))
        raise
    for line in lines:
        print('chao: config ' + line)


def handlebar(C):
    if not C.is_last_bar():
        return
    found, errors = day_signals(C.chao)
    for sid, symbol, side, date in found:
        print('chao: signal {} strategy={} {} {}'.format(date, sid, symbol, side))
    for sid, symbol, message in errors:
        print('chao: skipped strategy={} {}: {}'.format(sid, symbol, message))
    print('chao: {} signals, {} skipped'.format(len(found), len(errors)))
