"""Account state and order placement through QMT's trading API.

Like chao.qmt_source, this receives the ContextInfo and imports nothing
from QMT. passorder and get_trade_detail_data are QMT globals, so they are
passed in as an `api` object; tests pass a fake.
"""
import json
from pathlib import Path
from chao.orders import Book, Holding
from chao.qmt_source import to_qmt

STOCK_BUY, STOCK_SELL = 23, 24
BY_SHARES = 1101      # volume is a share count
LATEST_PRICE = 5      # backtest: fill at the bar close
LIMIT_PRICE = 11      # live: a specified limit price
STRATEGY_NAME = 'chao'
# quickTrade 2 sends the order on the bar that produced the signal, also on
# historical bars in a backtest; 0 would wait for the next bar.
QUICK = 2


class QmtApi:
    """The QMT globals the trade layer uses."""

    def __init__(self, passorder, get_trade_detail_data):
        self.passorder = passorder
        self.get_trade_detail_data = get_trade_detail_data


def read_book(api, account_id, owned, live):
    accounts = api.get_trade_detail_data(account_id, 'STOCK', 'ACCOUNT')
    if not accounts:
        raise RuntimeError('QMT returned no account data for {!r}'.format(account_id))
    holdings = {}
    for p in api.get_trade_detail_data(account_id, 'STOCK', 'POSITION'):
        symbol = p.m_strExchangeID + p.m_strInstrumentID
        holdings[symbol] = Holding(int(p.m_nVolume), int(p.m_nCanUseVolume))
    # Forget owned symbols that are no longer held (sold, or never filled).
    held = {s for s, h in holdings.items() if h.volume > 0}
    # Today's orders placed under chao's strategy name (live only).
    ordered = ({o.m_strExchangeID + o.m_strInstrumentID
                for o in api.get_trade_detail_data(account_id, 'STOCK', 'ORDER', STRATEGY_NAME)}
               if live else set())
    return Book(float(accounts[0].m_dAvailable), float(accounts[0].m_dBalance), holdings,
                set(owned) & held, ordered)


def place(api, C, account_id, order, price=None):
    """Send an order: at a limit price when given (live), else at the latest
    price (backtest, where that is the bar close)."""
    op = STOCK_BUY if order.side == 'buy' else STOCK_SELL
    pr_type, value = (LATEST_PRICE, -1) if price is None else (LIMIT_PRICE, price)
    api.passorder(op, BY_SHARES, account_id, to_qmt(order.symbol), pr_type, value, order.volume,
                  STRATEGY_NAME, QUICK, 'chao-s{}'.format(order.strategy), C)


class Ledger:
    """The stocks chao bought: the strategy that bought each one and whether
    its sell is pending. A pending sell is retried every day until the
    position is gone, as a TDX backtest sell always completes. Persisted as
    JSON {symbol: {"strategy": id, "selling": bool}} when a path is set."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.entries = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}

    @property
    def owned(self):
        return set(self.entries)

    def owner(self, symbol):
        return self.entries[symbol]['strategy']

    def selling(self, symbol):
        return self.entries[symbol]['selling']

    def keep(self, held):
        """Forget symbols no longer held (sold, or a buy that never filled)."""
        self.entries = {s: e for s, e in self.entries.items() if s in held}
        self.save()

    def bought(self, symbol, strategy):
        self.entries[symbol] = {'strategy': strategy, 'selling': False}
        self.save()

    def sell(self, symbol):
        self.entries[symbol]['selling'] = True
        self.save()

    def save(self):
        if self.path:
            self.path.write_text(json.dumps(self.entries, sort_keys=True))

    def check_persistence(self):
        """Write the file twice and read it back. Every update replaces the
        file, and QMT's sandbox refuses some file IO (it refused D:\\chao in
        init); a refusal must stop the run before a buy, not lose the record
        of who owns a position after it."""
        self.save()
        self.save()
        back = json.loads(self.path.read_text())
        if back != self.entries:
            raise OSError('the ledger {} read back {!r}, not what was written'.format(self.path, back))
