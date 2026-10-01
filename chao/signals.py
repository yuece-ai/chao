"""Signals of strategies on stocks, from any MarketData."""
from chao.formulas import bind, referenced_indices, signals
from chao.market import MissingInput, board_index, share_series

MIN_BARS = 260  # MA(250) plus REF lookback


def stock_signals(strategies, market, symbol):
    """(bars, {strategy id: buy/sell DataFrame}) for one stock; no signals when history is short.

    The stock's data is read once and indicator calls that only depend on it
    are shared across the strategies.
    """
    frame = market.bars(symbol)
    if len(frame) < MIN_BARS:
        return frame, {}
    indices = market.index_closes()
    bound = bind(frame, indices, indices[board_index(symbol)], market.name(symbol),
                 share_series(market.total_shares(symbol), frame.index))
    memo = {}
    return frame, {s.id: signals(s, bound, frame.index, memo) for s in strategies}


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
    """Yield (symbol, {strategy id: buy/sell DataFrame}) for each stock, or
    (symbol, error message) when its data is missing."""
    for symbol in symbols:
        strategies = strategies_for(context, symbol)
        if not strategies:
            continue
        try:
            yield symbol, stock_signals(strategies, context.market, symbol)[1]
        except (MissingInput, KeyError, ValueError) as exc:
            yield symbol, '{}: {}'.format(type(exc).__name__, exc)


def last_bar_signals(context, symbols):
    """Signals on each stock's last bar.

    Returns ([(date, strategy, symbol, 'buy'|'sell')], [(strategy, symbol, error)]).
    """
    found, errors = [], []
    for symbol, result in history_signals(context, symbols):
        if isinstance(result, str):
            errors += [(s.id, symbol, result) for s in strategies_for(context, symbol)]
            continue
        for sid, sig in result.items():
            last = sig.iloc[-1]
            for side in ('buy', 'sell'):
                if last[side]:
                    found.append((sig.index[-1].strftime('%Y-%m-%d'), sid, symbol, side))
    return sorted(found), sorted(errors)
