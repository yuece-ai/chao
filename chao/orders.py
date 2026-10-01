"""Turn one day's signals into orders for one shared stock account.

Pure: account state and prices come in as data, orders go out as data.
Rules:
- Only stocks in `owned` (bought by chao) are ever sold, so holdings the
  user opened by hand are left alone. Sells use the sellable volume, which
  is how T+1 is respected.
- At most max_positions chao-owned stocks are held; a stock already in the
  account (owned or not), or with a chao order today, is never bought again.
  The order check keeps a restarted live run from repeating unfilled buys. Each buy targets
  total_asset / max_positions, capped by the cash left.
- Conflicts follow the strategy priority (highest first): buy candidates
  are taken in (priority, symbol) order, and a stock signalled by several
  strategies is attributed to the highest-priority one.
- Quantities are whole lots of 100 shares; STAR market (SH688/689) orders
  need at least 200 shares.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, NamedTuple, Set

LOT = 100
STAR_MIN = 200
CASH_BUFFER = 0.002  # left unspent per buy for fees and price drift


class Holding(NamedTuple):
    volume: int
    sellable: int


class Book(NamedTuple):
    cash: float
    total_asset: float
    holdings: Dict[str, Holding]   # every position in the account
    owned: Set[str]                # symbols chao bought and still tracks
    ordered: Set[str]              # symbols with a chao order today (live)


class Order(NamedTuple):
    side: str        # 'buy' or 'sell'
    symbol: str
    volume: int
    strategy: int


def limit_price(side, last, margin, down=None, up=None):
    """Limit price `margin` through the last price (above for a buy, below for
    a sell), in cents and within the day's price limits when known."""
    target = last * (1 + margin) if side == 'buy' else last * (1 - margin)
    price = float(Decimal(repr(target)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    if side == 'buy' and up:
        price = min(price, float(up))
    if side == 'sell' and down:
        price = max(price, float(down))
    return price


def lot_volume(symbol, value, price):
    """Whole-lot shares worth at most value; 0 below the board minimum."""
    volume = int(value / price) // LOT * LOT
    minimum = STAR_MIN if symbol.startswith(('SH688', 'SH689')) else LOT
    return volume if volume >= minimum else 0


def plan_orders(signals, book, prices, max_positions, priority):
    """signals: [(strategy, symbol, 'buy'|'sell')]; prices: unadjusted {symbol: price};
    priority: strategy ids, highest priority first."""
    rank = {sid: i for i, sid in enumerate(priority)}
    ordered = sorted(signals, key=lambda s: (rank[s[0]], s[1], s[2]))
    orders: List[Order] = []
    sold = set()
    for sid, symbol, side in ordered:
        holding = book.holdings.get(symbol)
        if side == 'sell' and symbol in book.owned and holding and holding.sellable > 0 and symbol not in sold:
            orders.append(Order('sell', symbol, holding.sellable, sid))
            sold.add(symbol)
    held = {s for s, h in book.holdings.items() if h.volume > 0}
    slots = max_positions - len((held & book.owned) - sold)
    target = book.total_asset / max_positions
    cash = book.cash
    bought = set()
    for sid, symbol, side in ordered:
        if side != 'buy' or symbol in held or symbol in book.ordered or symbol in bought or slots <= 0:
            continue
        price = prices.get(symbol)
        if not price:
            continue
        volume = lot_volume(symbol, min(target, cash) * (1 - CASH_BUFFER), price)
        if volume == 0:
            continue
        orders.append(Order('buy', symbol, volume, sid))
        cash -= volume * price
        bought.add(symbol)
        slots -= 1
    return orders
