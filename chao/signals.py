"""Signals of one strategy on one stock, from any MarketData."""
from chao.formulas import signals
from chao.market import board_index, share_series

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
