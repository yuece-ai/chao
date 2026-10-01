"""MarketData from the built-in QMT client (a ContextInfo object).

This is the only module that knows QMT's API, symbol format and units. It
never imports QMT modules; the ContextInfo is passed in. Unadjusted bars are
forward-adjusted with chao.qfq, the algorithm verified against TDX.

Call shapes follow the official built-in API docs (dict.thinktrader.net,
innerApi); anything unexpected raises instead of being guessed at.

Data is read in two layers:
- per stock, cached for the run and refreshed with reset_static(): ex-rights
  factors, total-share history and the name;
- bars, read with prefetch(symbols) in batches; only the current batch is
  kept, so a full-history backtest does not hold every stock in memory.
"""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import numpy as np
import pandas as pd
from chao.market import INDEX_SYMBOLS, MarketData, MissingInput
from chao.qfq import ExRights, forward_adjust

BAR_FIELDS = ['open', 'high', 'low', 'close', 'amount']
HISTORY_BARS = 400  # live: MA(250) plus REF lookback, with margin
ALL_BARS = -1       # backtest: every local bar up to the backtest end
TOTAL_CAPITAL = 'CAPITALSTRUCTURE.total_capital'  # shares
# Formula index code -> QMT code. TDX's 999999 is QMT's 000001.SH.
QMT_INDEX = {'999999': '000001.SH', '399001': '399001.SZ', '399006': '399006.SZ',
             '000688': '000688.SH', '899050': '899050.BJ'}
# get_divid_factors values, in the documented order: 每股股利, 每股红股,
# 每股转增, 配股, 配股价, 是否股改, 除权系数 (amounts per share); keys are
# epoch milliseconds.
DIVID_COLUMNS = ['interest', 'stockBonus', 'stockGift', 'allotNum', 'allotPrice', 'gugai', 'dr']


def to_qmt(symbol):
    """'SH600000' -> '600000.SH'."""
    return '{}.{}'.format(symbol[2:], symbol[:2])


def from_qmt(code):
    """'600000.SH' -> 'SH600000'."""
    number, market = code.split('.')
    return market + number


def three_decimals(value):
    return Fraction(str(Decimal(repr(float(value))).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)))


def per_ten(value):
    """Per-share quantity -> per-10-shares Fraction at TDX's three decimals."""
    return three_decimals(float(value) * 10)


def ex_rights_from_divid(factors, as_of):
    """QMT get_divid_factors -> chao.qfq.ExRights, oldest first.

    Expected per QMT docs: {date: [interest, stockBonus, stockGift, allotNum,
    allotPrice, gugai, dr]} with the date as 'YYYYMMDD' or epoch milliseconds.
    """
    events = []
    for key, row in factors.items():
        if len(row) != len(DIVID_COLUMNS):
            raise MissingInput('unexpected get_divid_factors row {!r}'.format(row))
        values = dict(zip(DIVID_COLUMNS, row))
        date = qmt_date(key)
        if date <= pd.Timestamp(as_of):
            events.append(ExRights(date, per_ten(values['interest']), three_decimals(values['allotPrice']),
                                   per_ten(values['stockBonus']) + per_ten(values['stockGift']),
                                   per_ten(values['allotNum'])))
    return sorted(events, key=lambda e: e.date)


def qmt_date(value):
    """QMT time -> Timestamp: YYYYMMDD (bar index), epoch milliseconds
    (get_bar_timetag, get_divid_factors) or a date/datetime value."""
    if isinstance(value, (int, float, np.integer)) or (isinstance(value, str) and value.isdigit()):
        text = str(int(value))
        if len(text) == 8:
            return pd.Timestamp(text)
        utc = pd.Timestamp(int(text), unit='ms').tz_localize('UTC')
        return utc.tz_convert('Asia/Shanghai').normalize().tz_localize(None)
    return pd.Timestamp(value).normalize()


def name_text(value):
    """QMT returns GBK text; decode it when it arrives as bytes."""
    return value.decode('gbk') if isinstance(value, bytes) else str(value)


class QmtMarket(MarketData):
    def __init__(self, context_info, sectors, history_bars, end_time):
        self.C = context_info          # rebound by the entry on every QMT call
        self.sectors = sectors
        self.history_bars = history_bars
        self.end_time = end_time       # 'YYYYMMDD' in a backtest, '' live
        self.reset_static()
        self._raw = {}
        self._index_closes = None

    def reset_static(self):
        self._events, self._shares, self._names = {}, {}, {}

    def _fetch(self, qmt_codes):
        data = self.C.get_market_data_ex(BAR_FIELDS, qmt_codes, period='1d', end_time=self.end_time,
                                         count=self.history_bars, dividend_type='none',
                                         fill_data=False, subscribe=False)
        frames = {}
        for code in qmt_codes:
            frame = data.get(code)
            if frame is None or frame.empty:
                continue
            frame = frame[BAR_FIELDS].copy()
            frame.index = pd.to_datetime([str(i)[:8] for i in frame.index], format='%Y%m%d')
            frames[code] = frame
        return frames

    def prefetch(self, symbols):
        """Read the bars of one batch of stocks and the indices in one call."""
        codes = [to_qmt(s) for s in symbols] + list(QMT_INDEX.values())
        frames = self._fetch(codes)
        missing = [c for c in QMT_INDEX.values() if c not in frames]
        if missing:
            raise MissingInput('no daily bars from QMT for indices {}; download them first'.format(missing))
        self._raw = {from_qmt(c): f for c, f in frames.items() if c not in QMT_INDEX.values()}
        self._index_closes = {code: frames[QMT_INDEX[code]]['close'] for code in INDEX_SYMBOLS}

    def universe(self):
        codes = set()
        for sector in self.sectors:
            codes.update(self.C.get_stock_list_in_sector(sector))
        return sorted(from_qmt(code) for code in codes)

    def raw_bars(self, symbol):
        if symbol not in self._raw:
            raise MissingInput('no daily bars from QMT for {}'.format(to_qmt(symbol)))
        return self._raw[symbol]

    def load_static(self, symbol):
        """Read and cache the per-stock data that does not change intraday."""
        if symbol not in self._events:
            self._events[symbol] = self.C.get_divid_factors(to_qmt(symbol)) or {}
        self.name(symbol)
        self.total_shares(symbol)

    def bars(self, symbol):
        raw = self.raw_bars(symbol)
        if symbol not in self._events:
            self._events[symbol] = self.C.get_divid_factors(to_qmt(symbol)) or {}
        events = ex_rights_from_divid(self._events[symbol], raw.index[-1])
        adjusted = forward_adjust(raw, events)
        adjusted['amount'] = raw['amount']
        return adjusted

    def unadjusted_close(self, symbol, date):
        """Traded (unadjusted) close used to size orders; None if unknown."""
        raw = self._raw.get(symbol)
        close = None if raw is None else raw['close'].get(pd.Timestamp(date))
        return None if close is None or close != close else float(close)

    def index_closes(self):
        if self._index_closes is None:
            raise MissingInput('index bars are read by prefetch()')
        return self._index_closes

    def name(self, symbol):
        if symbol not in self._names:
            detail = self.C.get_instrumentdetail(to_qmt(symbol)) or {}
            self._names[symbol] = name_text(detail.get('InstrumentName', '')) or None
        return self._names[symbol]

    def total_shares(self, symbol):
        """Total-share history: one stock over a date range is a DataFrame
        indexed by date with one column per field (shares)."""
        if symbol not in self._shares:
            table = self.C.get_financial_data([TOTAL_CAPITAL], [to_qmt(symbol)], '19900101', '20991231',
                                              report_type='announce_time')
            if isinstance(table, pd.DataFrame) and table.shape[1] == 1:
                column = table.iloc[:, 0].dropna()
            elif isinstance(table, pd.DataFrame) and table.empty:
                column = pd.Series(dtype=float)
            else:
                raise MissingInput('unexpected get_financial_data result {!r}'.format(type(table)))
            self._shares[symbol] = {qmt_date(k).strftime('%Y-%m-%d'): float(v) for k, v in column.items()}
        return self._shares[symbol]
