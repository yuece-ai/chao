"""MarketData from the built-in QMT client (a ContextInfo object).

This is the only module that knows QMT's API, symbol format and units. It
never imports QMT modules; the ContextInfo is passed in. Unadjusted bars are
forward-adjusted with chao.qfq, the algorithm verified against TDX.

Shapes marked "per QMT docs" are not yet confirmed by the probe; anything
unexpected raises instead of being guessed at.
"""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import pandas as pd
from chao.market import INDEX_SYMBOLS, MarketData, MissingInput
from chao.qfq import ExRights, forward_adjust

BAR_FIELDS = ['open', 'high', 'low', 'close', 'amount']
HISTORY_BARS = 400  # live: MA(250) plus REF lookback, with margin
ALL_BARS = -1       # backtest: every bar QMT has locally
# Formula index code -> QMT code. TDX's 999999 is QMT's 000001.SH.
QMT_INDEX = {'999999': '000001.SH', '399001': '399001.SZ', '399006': '399006.SZ',
             '000688': '000688.SH', '899050': '899050.BJ'}
# get_divid_factors row order, per QMT docs (all amounts per share).
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
    """'20240527', 20240527 or epoch milliseconds -> Timestamp."""
    text = str(value)
    if len(text) == 8:
        return pd.Timestamp(text)
    return pd.Timestamp(int(value), unit='ms').tz_localize('UTC').tz_convert('Asia/Shanghai').normalize().tz_localize(None)


class QmtMarket(MarketData):
    def __init__(self, context_info, sectors, history_bars):
        self.C = context_info
        self.sectors = sectors
        self.history_bars = history_bars
        self._index_closes = None
        self._raw = {}  # symbol -> unadjusted bars, kept for order sizing

    def _unadjusted(self, qmt_codes):
        data = self.C.get_market_data_ex(BAR_FIELDS, qmt_codes, period='1d', count=self.history_bars,
                                         dividend_type='none', fill_data=False, subscribe=False)
        frames = {}
        for code in qmt_codes:
            frame = data.get(code)
            if frame is None or frame.empty:
                raise MissingInput('no daily bars from QMT for {}'.format(code))
            frame = frame[BAR_FIELDS].copy()
            frame.index = pd.to_datetime([str(i)[:8] for i in frame.index], format='%Y%m%d')
            frames[code] = frame
        return frames

    def universe(self):
        codes = set()
        for sector in self.sectors:
            codes.update(self.C.get_stock_list_in_sector(sector))
        return sorted(from_qmt(code) for code in codes)

    def bars(self, symbol):
        code = to_qmt(symbol)
        raw = self._raw[symbol] = self._unadjusted([code])[code]
        events = ex_rights_from_divid(self.C.get_divid_factors(code), raw.index[-1])
        adjusted = forward_adjust(raw, events)
        adjusted['amount'] = raw['amount']
        return adjusted

    def unadjusted_close(self, symbol, date):
        """Traded (unadjusted) close used to size orders; None if unknown."""
        raw = self._raw.get(symbol)
        if raw is None:
            raw = self._raw[symbol] = self._unadjusted([to_qmt(symbol)])[to_qmt(symbol)]
        close = raw['close'].get(pd.Timestamp(date))
        return None if close is None or close != close else float(close)

    def index_closes(self):
        if self._index_closes is None:
            frames = self._unadjusted(list(QMT_INDEX.values()))
            self._index_closes = {code: frames[QMT_INDEX[code]]['close'] for code in INDEX_SYMBOLS}
        return self._index_closes

    def name(self, symbol):
        return self.C.get_stock_name(to_qmt(symbol)) or None

    def total_shares(self, symbol):
        """Total-share history, per QMT docs: a date-indexed Series of shares."""
        series = self.C.get_financial_data(['CAPITALSTRUCTURE.total_capital'], [to_qmt(symbol)],
                                           '19900101', '20991231', report_type='announce_time')
        if not isinstance(series, pd.Series):
            raise MissingInput('unexpected get_financial_data result {!r}'.format(type(series)))
        series = series.dropna()
        return {qmt_date(k).strftime('%Y-%m-%d'): float(v) for k, v in series.items()}
