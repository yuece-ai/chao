"""Independent-symbol replay of the TDX backtester's single-precision ledger.

The ledger rules were derived from the fields of the TDX signal exports
(quantity, amount, fee, profit and available cash) and reproduce them to the
displayed cent.  TDX fills at the bar close without slippage and keeps every
money value as float32; displayed values are rounded only for output.
"""
import functools
import math
from typing import NamedTuple
import numpy as np
import pandas as pd

# Direction labels are the literal values of the TDX export's signal column.
BUY, SELL, FLATTEN = '买开', '卖平', '平盘'


@functools.lru_cache(maxsize=None)
def day(text):
    """'YYYY-MM-DD' -> Timestamp, parsed once per distinct date."""
    return pd.Timestamp(text)


def f32(value):
    return float(np.float32(value))


class ReplaySpec(NamedTuple):
    start: str
    end: str
    initial_cash: float
    buy_fee_rate: float
    sell_fee_rate: float


class Position(NamedTuple):
    quantity: int
    price: float
    fee: float
    cash_before: float


def buy_fill(cash, close, spec):
    """Return (position, cash_after, event_fields) or None when unaffordable."""
    price = f32(close)
    quantity = math.floor(cash / (price * f32(1 + spec.buy_fee_rate)))
    if quantity <= 0:
        return None
    amount = f32(quantity * price)
    fee = f32(amount * spec.buy_fee_rate)
    # TDX may overdraw by a fraction of a cent here and then clamps at zero.
    cash_after = max(0.0, f32(cash - amount - fee))
    position = Position(quantity, price, fee, cash)
    return position, cash_after, dict(price=price, quantity=quantity, amount=amount,
                                      fee=fee, profit=0.0, cash=cash_after)


def sell_fill(position, close, spec):
    """Return (cash_after, event_fields) for closing the whole position."""
    price = f32(close)
    amount = f32(position.quantity * price)
    fee = f32(amount * spec.sell_fee_rate)
    profit = f32(position.quantity * f32(price - position.price))
    cash_after = f32(f32(f32(position.cash_before - position.fee) + profit) - fee)
    return cash_after, dict(price=price, quantity=position.quantity, amount=amount,
                            fee=fee, profit=profit, cash=cash_after)


def replay(frame, signals, spec):
    """(events, summary) of one stock replayed with the TDX ledger rules."""
    if not signals.dates.equals(frame.index):
        raise ValueError('signals are not on the bars of the stock they replay')
    # Positions, not .loc with date strings: pandas 0.22 (QMT) parses the
    # strings on every call, which was 40% of a one-day backtest.
    first, stop = frame.index.searchsorted(day(spec.start)), frame.index.searchsorted(day(spec.end), side='right')
    buys = signals.buy[first:stop]
    if not buys.any():  # no position can open: nothing to replay
        return [], {'closed_trades': 0, 'profitable_trades': 0, 'net_profit': 0.0, 'fees': 0,
                    'max_drawdown': 0.0}
    dates = frame.index[first:stop]
    closes = frame['close'].values[first:stop].tolist()
    buys, sells = buys.tolist(), signals.sell[first:stop].tolist()
    cash = f32(spec.initial_cash); position = None; events = []
    equity = []
    for i, close in enumerate(closes):
        # TDX checks the buy before the sell, so a bar that meets both
        # conditions opens and closes a position at the same close.
        if not position and buys[i]:
            fill = buy_fill(cash, close, spec)
            if fill:
                position, cash, fields = fill
                events.append(dict(date=dates[i].strftime('%Y-%m-%d'), direction=BUY, **fields))
        if position and sells[i]:
            cash, fields = sell_fill(position, close, spec); position = None
            events.append(dict(date=dates[i].strftime('%Y-%m-%d'), direction=SELL, **fields))
        equity.append(cash + (position.quantity * close if position else 0.0))
    if position:
        cash, fields = sell_fill(position, closes[-1], spec); position = None
        events.append(dict(date=dates[-1].strftime('%Y-%m-%d'), direction=FLATTEN, **fields))
    curve = np.array(equity, dtype=float)
    sold = [e for e in events if e['direction'] != BUY]
    summary = {'closed_trades': len(sold), 'profitable_trades': sum(e['profit'] > 0 for e in sold),
               'net_profit': cash - spec.initial_cash, 'fees': sum(e['fee'] for e in events),
               'max_drawdown': float((np.maximum.accumulate(curve) - curve).max()) if len(curve) else 0.0}
    return events, summary
