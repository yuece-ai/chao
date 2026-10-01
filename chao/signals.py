"""Signals of one strategy on one stock, from any MarketData."""
from chao.formulas import signals
from chao.market import MissingInput, board_index, share_series

MIN_BARS = 260  # MA(250) plus REF lookback


def stock_signals(strategy, market, symbol):
    """(bars, buy/sell DataFrame); signals are None when history is too short."""
    frame = market.bars(symbol)
    if len(frame) < MIN_BARS:
        return frame, None
    indices = market.index_closes()
    sig = signals(strategy, frame, indices, indices[board_index(symbol)],
                  market.name(symbol), share_series(market.total_shares(symbol), frame.index))
    return frame, sig


def strategy_universe(sid, symbols):
    """Strategy 7 trades the Beijing exchange; strategies 1-6 trade SH and SZ."""
    return [s for s in symbols if (sid == 7) == s.startswith('BJ')]


def scan(context, last_only):
    """Signals of every enabled strategy over its universe.

    Returns ([(date, strategy, symbol, 'buy'|'sell')], [(strategy, symbol, error)]).
    last_only keeps the last bar of each stock; otherwise every bar is kept.
    """
    symbols = context.market.universe()
    found, errors = [], []
    for sid, strategy in sorted(context.strategies.items()):
        for symbol in strategy_universe(sid, symbols):
            try:
                _, sig = stock_signals(strategy, context.market, symbol)
            except (MissingInput, KeyError, ValueError) as exc:
                errors.append((sid, symbol, '{}: {}'.format(type(exc).__name__, exc)))
                continue
            if sig is None:
                continue
            rows = sig.tail(1) if last_only else sig
            for side in ('buy', 'sell'):
                for date in rows.index[rows[side].values.astype(bool)]:
                    found.append((date.strftime('%Y-%m-%d'), sid, symbol, side))
    return sorted(found), errors
