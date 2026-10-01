"""MarketData from local TDX files: unadjusted .day bars, a qfq root built by
scripts/build_qfq.py, and GBBQ share-capital records."""
from chao.data import equity_files, load_prices
from chao.gbbq import total_shares
from chao.market import INDEX_SYMBOLS, MarketData

TEN_THOUSAND = 10000.0  # GBBQ share capital is stored in units of 10,000 shares


class TdxMarket(MarketData):
    def __init__(self, raw_root, qfq_root, gbbq, names):
        self.raw_root, self.qfq_root = raw_root, qfq_root
        self.gbbq, self.names = gbbq, names
        self._index_closes = None

    def universe(self):
        return sorted(symbol for symbol, _ in equity_files(self.raw_root))

    def bars(self, symbol):
        return load_prices(self.raw_root, self.qfq_root, symbol)

    def index_closes(self):
        if self._index_closes is None:
            self._index_closes = {code: self.bars(symbol)['close'] for code, symbol in INDEX_SYMBOLS.items()}
        return self._index_closes

    def name(self, symbol):
        return self.names.get(symbol[2:])

    def total_shares(self, symbol):
        steps = total_shares(self.gbbq.get(symbol[2:], []))
        return {d: float(v) * TEN_THOUSAND for d, v in steps.items()}
