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


def divid_events(factors):
    """QMT get_divid_factors -> every chao.qfq.ExRights, oldest first."""
    events = []
    for key, row in factors.items():
        if len(row) != len(DIVID_COLUMNS):
            raise MissingInput('unexpected get_divid_factors row {!r}'.format(row))
        values = dict(zip(DIVID_COLUMNS, row))
        events.append(ExRights(qmt_date(key), per_ten(values['interest']), three_decimals(values['allotPrice']),
                               per_ten(values['stockBonus']) + per_ten(values['stockGift']),
                               per_ten(values['allotNum'])))
    return sorted(events, key=lambda e: e.date)


def ex_rights_from_divid(factors, as_of):
    """Ex-rights events on or before as_of (GBBQ-style snapshot)."""
    return [e for e in divid_events(factors) if e.date <= pd.Timestamp(as_of)]


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


TICK_FIELDS = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'lastPrice', 'amount': 'amount'}


def tick_date(tick):
    """Day of a tick: timetag is '20240527 14:50:03' or epoch milliseconds."""
    stamp = tick.get('timetag')
    if isinstance(stamp, str) and len(stamp) >= 8 and stamp[:8].isdigit():
        return pd.Timestamp(stamp[:8])
    return qmt_date(stamp) if stamp else None


def with_tick(frame, tick, day):
    """frame with today's bar set from a get_full_tick tick, if the tick is
    from today and has traded; otherwise frame unchanged (None stays None)."""
    if not tick or float(tick.get('lastPrice') or 0) <= 0 or tick_date(tick) != day:
        return frame
    row = np.array([[float(tick[TICK_FIELDS[k]]) for k in BAR_FIELDS]])
    if frame is None:
        return pd.DataFrame(row, index=pd.DatetimeIndex([day]), columns=BAR_FIELDS)
    keep = frame.index < day
    return pd.DataFrame(np.vstack([frame[BAR_FIELDS].values[keep], row]),
                        index=frame.index[keep].append(pd.DatetimeIndex([day])), columns=BAR_FIELDS)


class QmtMarket(MarketData):
    def __init__(self, context_info, sectors, history_bars, end_time):
        self.C = context_info          # rebound by the entry on every QMT call
        self.sectors = sectors
        self.history_bars = history_bars
        self.end_time = end_time       # 'YYYYMMDD' in a backtest, '' live
        self.reset_static()
        self._raw, self._ticks, self._day = {}, {}, None
        self._index_closes = None

    def reset_static(self):
        self._events, self._shares, self._details = {}, {}, {}

    def detail(self, symbol):
        """Contract details: name and today's price limits. Current clients
        call it get_instrument_detail; older ones get_instrumentdetail."""
        if symbol not in self._details:
            read = getattr(self.C, 'get_instrument_detail', None) or self.C.get_instrumentdetail
            self._details[symbol] = read(to_qmt(symbol)) or {}
        return self._details[symbol]

    def price_limits(self, symbol):
        """(跌停价, 涨停价) for today, or (None, None) when unknown."""
        d = self.detail(symbol)
        return d.get('DownStopPrice') or None, d.get('UpStopPrice') or None

    def latest_prices(self, symbols, today):
        """{symbol: last price} from get_full_tick, for stocks traded today."""
        day = pd.Timestamp(today)
        ticks = self.C.get_full_tick([to_qmt(s) for s in symbols]) or {}
        prices = {}
        for s in symbols:
            tick = ticks.get(to_qmt(s))
            if tick and float(tick.get('lastPrice') or 0) > 0 and tick_date(tick) == day:
                prices[s] = float(tick['lastPrice'])
        return prices

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

    def prefetch(self, symbols, index_codes, today=None):
        """Read the bars of one batch of stocks and the needed indices in one
        call. Live (today given), today's bar comes from the latest ticks:
        subscribe=False reads local data, which ends yesterday intraday."""
        qmt_index = {code: QMT_INDEX[code] for code in sorted(index_codes)}
        codes = [to_qmt(s) for s in symbols] + list(qmt_index.values())
        frames = self._fetch(codes)
        self._day = None if today is None else pd.Timestamp(today)
        self._ticks = {} if today is None else (self.C.get_full_tick(codes) or {})
        for c in qmt_index.values():
            frames[c] = with_tick(frames.get(c), self._ticks.get(c), self._day)
        missing = [c for c in qmt_index.values() if frames[c] is None]
        if missing:
            raise MissingInput('no daily bars from QMT for indices {}; download them first'.format(missing))
        # Stocks get today's tick when first used (see raw_bars).
        self._raw = {from_qmt(c): frames.get(c) for c in codes if c not in qmt_index.values()}
        self._index_closes = {code: frames[c]['close'] for code, c in qmt_index.items()}

    def universe(self):
        codes = set()
        for sector in self.sectors:
            codes.update(self.C.get_stock_list_in_sector(sector))
        return sorted(from_qmt(code) for code in codes)

    def last_bar_date(self, symbol):
        try:
            return self.raw_bars(symbol).index[-1]
        except MissingInput:
            return None

    def raw_bars(self, symbol):
        code = to_qmt(symbol)
        if code in self._ticks:
            self._raw[symbol] = with_tick(self._raw.get(symbol), self._ticks.pop(code), self._day)
        raw = self._raw.get(symbol)
        if raw is None or raw.empty:
            raise MissingInput('no daily bars from QMT for {}'.format(code))
        return raw

    def load_static(self, symbol):
        """Read and cache the per-stock data that does not change intraday."""
        if symbol not in self._events:
            self._events[symbol] = divid_events(self.C.get_divid_factors(to_qmt(symbol)) or {})
        self.detail(symbol)
        self.total_shares(symbol)

    def bars(self, symbol):
        raw = self.raw_bars(symbol)
        if symbol not in self._events:
            self._events[symbol] = divid_events(self.C.get_divid_factors(to_qmt(symbol)) or {})
        events = [e for e in self._events[symbol] if e.date <= raw.index[-1]]
        adjusted = forward_adjust(raw, events)
        adjusted['amount'] = raw['amount']
        return adjusted

    def unadjusted_close(self, symbol, date):
        """Traded (unadjusted) close used to size orders; None if unknown."""
        try:
            raw = self.raw_bars(symbol)
        except MissingInput:
            return None
        close = raw['close'].get(pd.Timestamp(date))
        return None if close is None or close != close else float(close)

    def index_closes(self):
        if self._index_closes is None:
            raise MissingInput('index bars are read by prefetch()')
        return self._index_closes

    def name(self, symbol):
        return name_text(self.detail(symbol).get('InstrumentName', '')) or None

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
