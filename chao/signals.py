"""Signals of strategies on stocks, from any MarketData.

Stocks are evaluated together as a panel: one column per stock, rows aligned
on each stock's own last bar (never on dates, so a suspension does not open
a gap in a rolling window). Each indicator then runs once per panel instead
of once per stock. Results are bit-identical to evaluating each stock alone.
"""
from typing import Any, Dict, List, NamedTuple
import numpy as np
import pandas as pd
from chao.formulas import bind, reads, referenced_indices, signals
from chao.market import MissingInput, board_index, share_series

MIN_BARS = 260  # MA(250) plus REF lookback


def stack(arrays, rows):
    """Bottom-aligned (rows, len(arrays)) panel; missing leading rows are NaN."""
    panel = np.full((rows, len(arrays)), np.nan)
    for j, values in enumerate(arrays):
        panel[rows - len(values):, j] = values
    return pd.DataFrame(panel)


def on_dates(series, dates):
    """series.reindex(dates).values for a sorted, unique series index."""
    known = series.index.values
    pos = np.searchsorted(known, dates.values)
    clipped = np.minimum(pos, len(known) - 1)
    hit = (pos < len(known)) & (known[clipped] == dates.values)
    return np.where(hit, series.values[clipped], np.nan)


class PanelView(NamedTuple):
    rows: int
    dates: List[Any]      # each stock's own bar dates (bottom-aligned in the panel)
    names: List[Any]
    shares: List[Any]     # FINANCE(1) per stock, or None
    bound: Dict[str, Any]


def bind_panel(market, symbols, frames):
    """Bind the formula names for stocks whose bars are given."""
    rows = max(len(f) for f in frames)
    dates = [f.index for f in frames]
    indices = market.index_closes()
    cached = {}
    def index_of(code):
        if code not in cached:
            cached[code] = stack([on_dates(indices[code], d) for d in dates], rows)
        return cached[code]
    names = [market.name(s) for s in symbols]
    shares = [share_series(market.total_shares(s), d) for s, d in zip(symbols, dates)]
    bound = bind(close=stack([f['close'].values for f in frames], rows),
                 high=stack([f['high'].values for f in frames], rows),
                 low=stack([f['low'].values for f in frames], rows),
                 amo=stack([f['amount'].values for f in frames], rows),
                 indexc=stack([on_dates(indices[board_index(s)], d) for s, d in zip(symbols, dates)], rows),
                 index_of=index_of, names=names,
                 shares=stack([np.full(len(d), np.nan) if x is None else x.values for x, d in zip(shares, dates)], rows))
    return PanelView(rows, dates, names, shares, bound)


def panel_signals(strategies, market, symbols):
    """Signals of the strategies on a batch of stocks.

    Returns (bars {symbol: DataFrame}, signals {symbol: {strategy id:
    buy/sell DataFrame}}, errors [(strategy, symbol, message)]). Stocks with
    fewer than MIN_BARS bars get no signals.
    """
    bars, errors = {}, []
    for symbol in symbols:
        try:
            bars[symbol] = market.bars(symbol)
        except (MissingInput, KeyError, ValueError) as exc:
            errors += [(s.id, symbol, '{}: {}'.format(type(exc).__name__, exc))
                       for s in strategies if strategy_universe(s.id, [symbol])]
    panel = [s for s in symbols if s in bars and len(bars[s]) >= MIN_BARS]
    if not panel:
        return bars, {}, sorted(errors)
    view = bind_panel(market, panel, [bars[s] for s in panel])
    rows, dates, names, shares, bound = view.rows, view.dates, view.names, view.shares, view.bound
    memo, result = {}, {s: {} for s in panel}
    for strategy in strategies:
        needs = reads(strategy)
        buy, sell = signals(strategy, bound, (rows, len(panel)), memo)
        for j, symbol in enumerate(panel):
            if not strategy_universe(strategy.id, [symbol]):
                continue
            if 'NAMELIKE' in needs and not names[j]:
                errors.append((strategy.id, symbol, 'MissingInput: NAMELIKE needs the stock name'))
                continue
            if 'FINANCE' in needs and shares[j] is None:
                errors.append((strategy.id, symbol, 'MissingInput: FINANCE(1) needs a share-capital series'))
                continue
            n = len(dates[j])
            result[symbol][strategy.id] = pd.DataFrame({'buy': buy[rows - n:, j], 'sell': sell[rows - n:, j]},
                                                       index=dates[j])
    return bars, result, sorted(errors)


def stock_signals(strategies, market, symbol):
    """(bars, {strategy id: buy/sell DataFrame}) for one stock; raises the
    first missing-data error, as the per-stock replay expects."""
    bars, result, errors = panel_signals(strategies, market, [symbol])
    if errors:
        raise MissingInput(errors[0][2])
    return bars[symbol], result.get(symbol, {})


def strategy_universe(sid, symbols):
    """Strategy 7 trades the Beijing exchange; strategies 1-6 trade SH and SZ."""
    return [s for s in symbols if (sid == 7) == s.startswith('BJ')]


# Board indices INDEXC can resolve to, per strategy universe (see board_index).
SH_SZ_BOARD_INDICES = {'999999', '399001', '399006', '000688'}
BJ_BOARD_INDICES = {'899050'}


def required_indices(strategies):
    """Index codes the given strategies can read, via INDEXC or "code$C"."""
    codes = set()
    for s in strategies:
        codes |= referenced_indices(s) | (BJ_BOARD_INDICES if s.id == 7 else SH_SZ_BOARD_INDICES)
    return codes


def strategies_for(context, symbol):
    return [s for _, s in sorted(context.strategies.items()) if strategy_universe(s.id, [symbol])]


def history_signals(context, symbols):
    """({symbol: {strategy id: buy/sell DataFrame}}, [(strategy, symbol, error)])
    for every enabled strategy on a batch of stocks."""
    _, result, errors = panel_signals([s for _, s in sorted(context.strategies.items())],
                                      context.market, symbols)
    return result, errors


def last_bar_signals(context, symbols):
    """Signals on each stock's last bar.

    Returns ([(date, strategy, symbol, 'buy'|'sell')], [(strategy, symbol, error)]).
    """
    result, errors = history_signals(context, symbols)
    found = []
    for symbol, by_strategy in result.items():
        for sid, sig in by_strategy.items():
            last = sig.iloc[-1]
            for side in ('buy', 'sell'):
                if last[side]:
                    found.append((sig.index[-1].strftime('%Y-%m-%d'), sid, symbol, side))
    return sorted(found), errors
