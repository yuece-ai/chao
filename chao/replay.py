"""Independent-symbol replay of the TDX backtester's single-precision ledger.

The ledger rules were derived from the fields of the TDX signal exports
(quantity, amount, fee, profit and available cash) and reproduce them to the
displayed cent.  TDX fills at the bar close without slippage and keeps every
money value as float32; displayed values are rounded only for output.
"""
import math
from dataclasses import dataclass
import numpy as np
import pandas as pd

# Direction labels are the literal values of the TDX export's signal column.
BUY, SELL, FLATTEN = '买开', '卖平', '平盘'


def f32(value):
    return float(np.float32(value))


@dataclass(frozen=True)
class ReplaySpec:
    start: str
    end: str
    initial_cash: float
    buy_fee_rate: float
    sell_fee_rate: float


@dataclass(frozen=True)
class Position:
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
    bars = frame.loc[spec.start:spec.end]
    cash = f32(spec.initial_cash); position = None; events = []; curve = []
    for date, row in bars.iterrows():
        sig = signals.loc[date]
        # TDX checks the buy before the sell, so a bar that meets both
        # conditions opens and closes a position at the same close.
        if not position and sig.buy:
            fill = buy_fill(cash, row.close, spec)
            if fill:
                position, cash, fields = fill
                events.append(dict(date=date.strftime('%Y-%m-%d'), direction=BUY, **fields))
        if position and sig.sell:
            cash, fields = sell_fill(position, row.close, spec); position = None
            events.append(dict(date=date.strftime('%Y-%m-%d'), direction=SELL, **fields))
        curve.append((date, cash + (position.quantity * row.close if position else 0.0)))
    if position:
        date = bars.index[-1]
        cash, fields = sell_fill(position, bars.close.iloc[-1], spec); position = None
        events.append(dict(date=date.strftime('%Y-%m-%d'), direction=FLATTEN, **fields))
    equity = pd.Series(dict(curve), dtype=float)
    sells = [e for e in events if e['direction'] != BUY]
    summary = {'closed_trades': len(sells), 'profitable_trades': sum(e['profit'] > 0 for e in sells),
               'net_profit': cash - spec.initial_cash, 'fees': sum(e['fee'] for e in events),
               'max_drawdown': float((equity.cummax() - equity).max()) if len(equity) else 0.0}
    return events, summary
