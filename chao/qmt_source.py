"""MarketData from the built-in QMT client (a ContextInfo object).

This is the only module that knows QMT's API, symbol format and units. It
never imports QMT modules; the ContextInfo is passed in. Unadjusted bars are
forward-adjusted with chao.qfq, the algorithm verified against TDX.

Call shapes follow the official built-in API docs (dict.thinktrader.net,
innerApi); anything unexpected raises instead of being guessed at.

Data is read in layers:
- per stock, cached for the day and refreshed with reset_static(): ex-rights
  factors, contract details (with today's total shares);
- backtest: bars read with prefetch(symbols) in batches; only the current
  batch is kept, so a full history does not sit in memory;
- live: prepare() reads every stock's last HISTORY_BARS local bars (up to
  yesterday, final for the day) and adjusts them once; load_today() then
  only appends today's bar from the latest ticks.
"""
import re
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import NamedTuple, Optional
import numpy as np
import pandas as pd
from chao.market import INDEX_SYMBOLS, MarketData, MissingInput
from chao.qfq import ExRights, forward_adjust

BAR_FIELDS = ['open', 'high', 'low', 'close', 'amount']
HISTORY_BARS = 400  # live: MA(250) plus REF lookback, with margin
ALL_BARS = -1       # backtest: every local bar up to the backtest end
SHARES_SINCE = '1990-01-01'  # today's total shares stand for every bar
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
TICK_AMOUNT_UNITS = (1, 100)  # tick amount per yuan: as documented, and as the client reported


def tick_date(tick):
    """Day of a tick: timetag is '20240527 14:50:03' or epoch milliseconds."""
    stamp = tick.get('timetag')
    if isinstance(stamp, str) and len(stamp) >= 8 and stamp[:8].isdigit():
        return pd.Timestamp(stamp[:8])
    return qmt_date(stamp) if stamp else None


def tick_amount_unit(tick):
    """How many 'amount' units make one yuan in this tick, read from the
    tick itself: amount / shares traded is the average price, which lies
    between the day's low and high. The client sent 600000.SH ticks for one
    day once in yuan and once at 100x, so every tick is checked.
    None when no unit fits."""
    shares, amount = float(tick.get('pvolume') or 0), float(tick.get('amount') or 0)
    if shares <= 0:
        return 1  # nothing traded
    for unit in TICK_AMOUNT_UNITS:
        if float(tick['low']) * 0.99 <= amount / unit / shares <= float(tick['high']) * 1.01:
            return unit
    return None


def with_tick(frame, tick, day, amount_unit=1):
    """frame with today's bar set from a get_full_tick tick, if the tick is
    from today and has traded; otherwise frame unchanged (None stays None).
    The amount is divided by amount_unit to give yuan, as in daily bars."""
    if not tick or float(tick.get('lastPrice') or 0) <= 0 or tick_date(tick) != day:
        return frame
    row = np.array([[float(tick[TICK_FIELDS[k]]) / (amount_unit if k == 'amount' else 1) for k in BAR_FIELDS]])
    if frame is None:
        return pd.DataFrame(row, index=pd.DatetimeIndex([day]), columns=BAR_FIELDS)
    keep = frame.index < day
    return pd.DataFrame(np.vstack([frame[BAR_FIELDS].values[keep], row]),
                        index=frame.index[keep].append(pd.DatetimeIndex([day])), columns=BAR_FIELDS)


class Quote(NamedTuple):
    last: float
    ask: Optional[float]   # best ask, the buy price cage's reference
    bid: Optional[float]   # best bid, the sell price cage's reference


def first_price(levels):
    """Best level of a tick's askPrice/bidPrice (a list, or one number)."""
    for value in (levels if isinstance(levels, (list, tuple)) else [levels]):
        if value and float(value) > 0:
            return float(value)
    return None


class QmtMarket(MarketData):
    def __init__(self, context_info, sectors, history_bars, end_time):
        self.C = context_info          # rebound by the entry on every QMT call
        self.sectors = sectors
        self.history_bars = history_bars
        self.end_time = end_time       # 'YYYYMMDD' in a backtest, '' live
        self.reset_static()
        self._raw = {}                 # backtest: unadjusted bars of the current batch
        self._index_closes = None
        self._prepared, self._prepared_index = {}, {}   # live: adjusted history, index bars
        self._today = None             # live: {symbol: prepared history + today's bar}
        self.bad_amount = []           # live: stocks whose tick amount fits no unit today

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

    def latest_quotes(self, symbols, today):
        """{symbol: Quote} from get_full_tick, for stocks traded today."""
        day = pd.Timestamp(today)
        ticks = self.C.get_full_tick([to_qmt(s) for s in symbols]) or {}
        quotes = {}
        for s in symbols:
            tick = ticks.get(to_qmt(s))
            if tick and float(tick.get('lastPrice') or 0) > 0 and tick_date(tick) == day:
                quotes[s] = Quote(float(tick['lastPrice']), first_price(tick.get('askPrice')),
                                  first_price(tick.get('bidPrice')))
        return quotes

    def latest_static_dates(self):
        """Latest ex-rights date and share-capital date read so far (or None)."""
        events = [e.date for es in self._events.values() for e in es]
        shares = [pd.Timestamp(d) for steps in self._shares.values() for d in steps]
        return max(events) if events else None, max(shares) if shares else None

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

    def prefetch(self, symbols, index_codes):
        """Backtest: read the bars of one batch of stocks and the needed
        indices in one call."""
        qmt_index = {code: QMT_INDEX[code] for code in sorted(index_codes)}
        codes = [to_qmt(s) for s in symbols] + list(qmt_index.values())
        frames = self._fetch(codes)
        missing = [c for c in qmt_index.values() if c not in frames]
        if missing:
            raise MissingInput('no daily bars from QMT for indices {}; download them first'.format(missing))
        self._raw = {from_qmt(c): f for c, f in frames.items() if c not in qmt_index.values()}
        self._index_closes = {code: frames[c]['close'] for code, c in qmt_index.items()}

    def download_daily(self, symbols, index_codes, download):
        """Ask QMT to append any missing daily bars to local data. The call is
        non-blocking; prepare() checks what actually arrived."""
        for code in [to_qmt(s) for s in symbols] + [QMT_INDEX[c] for c in sorted(index_codes)]:
            download(code, '1d', '', '')  # an empty start downloads incrementally

    def prepare(self, symbols, index_codes, today, batch):
        """Live: every stock's local history before today, adjusted with the
        ex-rights known today, and the index bars. Returns {symbol or index
        code: last local bar date} for the readiness check."""
        day = pd.Timestamp(today)
        self._prepared, self._prepared_index, self._today = {}, {}, None
        qmt_index = {code: QMT_INDEX[code] for code in sorted(index_codes)}
        last = {}
        for i in range(0, len(symbols), batch):
            part = symbols[i:i + batch]
            codes = [to_qmt(s) for s in part] + (list(qmt_index.values()) if i == 0 else [])
            for code, frame in self._fetch(codes).items():
                frame = frame[frame.index < day]
                if frame.empty:
                    continue
                if code in qmt_index.values():
                    index = next(k for k, v in qmt_index.items() if v == code)
                    self._prepared_index[index] = frame
                    last[index] = frame.index[-1]
                    continue
                symbol = from_qmt(code)
                events = [e for e in self.ex_rights(symbol) if e.date <= day]
                adjusted = forward_adjust(frame, events)
                adjusted['amount'] = frame['amount']
                self._prepared[symbol] = adjusted
                last[symbol] = frame.index[-1]
        return last

    def load_today(self, symbols, today):
        """Live: prepared history plus today's bar from one get_full_tick
        call, for a batch of stocks and the indices. Today's bar is the
        unadjusted price, as forward adjustment leaves it."""
        day = pd.Timestamp(today)
        codes = [to_qmt(s) for s in symbols] + [QMT_INDEX[c] for c in sorted(self._prepared_index)]
        ticks = self.C.get_full_tick(codes) or {}
        self._today = {}
        for s in (s for s in symbols if s in self._prepared):
            tick = ticks.get(to_qmt(s))
            unit = tick_amount_unit(tick) if tick else 1
            if unit is None:  # no bar today rather than a wrong AMO
                self.bad_amount.append(s)
                tick = None
            self._today[s] = with_tick(self._prepared[s], tick, day, unit)
        self._index_closes = {code: with_tick(frame, ticks.get(QMT_INDEX[code]), day)['close']
                              for code, frame in self._prepared_index.items()}

    def universe(self):
        codes = set()
        for sector in self.sectors:
            codes.update(self.C.get_stock_list_in_sector(sector))
        return sorted(from_qmt(code) for code in codes)

    def last_bar_date(self, symbol):
        """Live: date of the last bar loaded for today (None without data)."""
        frame = (self._today or {}).get(symbol)
        return None if frame is None else frame.index[-1]

    def raw_bars(self, symbol):
        raw = self._raw.get(symbol)
        if raw is None or raw.empty:
            raise MissingInput('no daily bars from QMT for {}'.format(to_qmt(symbol)))
        return raw

    def load_static(self, symbol):
        """Read and cache the per-stock data that does not change intraday."""
        if symbol not in self._events:
            self._events[symbol] = divid_events(self.C.get_divid_factors(to_qmt(symbol)) or {})
        self.detail(symbol)
        self.total_shares(symbol)

    def ex_rights(self, symbol):
        """Every ex-rights event of a stock, oldest first (cached for the day)."""
        if symbol not in self._events:
            self._events[symbol] = divid_events(self.C.get_divid_factors(to_qmt(symbol)) or {})
        return self._events[symbol]

    def bars(self, symbol):
        if self._today is not None:
            frame = self._today.get(symbol)
            if frame is None:
                raise MissingInput('no local daily bars for {}'.format(to_qmt(symbol)))
            return frame
        raw = self.raw_bars(symbol)
        if symbol not in self._events:
            self._events[symbol] = divid_events(self.C.get_divid_factors(to_qmt(symbol)) or {})
        events = [e for e in self._events[symbol] if e.date <= raw.index[-1]]
        adjusted = forward_adjust(raw, events)
        adjusted['amount'] = raw['amount']
        return adjusted

    def index_closes(self):
        if self._index_closes is None:
            raise MissingInput('index bars are read by prefetch()')
        return self._index_closes

    def name(self, symbol):
        return name_text(self.detail(symbol).get('InstrumentName', '')) or None

    def total_shares(self, symbol):
        """Total shares for FINANCE(1): today's TotalVolume from the contract
        details, for live runs and backtests alike. The client holds no share
        history (get_financial_data returned all NaN); the TDX exports used
        the history, so strategies 1, 4 and 5 miss a few backtest trades."""
        if symbol not in self._shares:
            shares = float(self.detail(symbol).get('TotalVolume') or 0)
            self._shares[symbol] = {SHARES_SINCE: shares} if shares > 0 else {}
        return self._shares[symbol]


# A long-listed stock with dividends and an index, read once at startup to
# confirm every API returns the shape this module parses.
PROBE_STOCK, PROBE_INDEX = '600000.SH', '000001.SH'
TICK_KEYS = ('timetag', 'lastPrice', 'open', 'high', 'low', 'amount', 'askPrice', 'bidPrice')
CODE = re.compile(r'^[0-9]{6}\.(SH|SZ|BJ)$')


def short(value):
    if isinstance(value, pd.DataFrame):
        return 'columns={} rows={} head={}'.format(list(value.columns), len(value), value.head(1).to_dict('records'))
    text = repr(value)
    return text if len(text) <= 200 else text[:200] + '...'


def probe(C, sectors, end_time, live, clock):
    """Call each QMT data API once and check the shapes parsed above.
    Returns log lines; raises MissingInput showing the unexpected value."""
    lines = []
    def seen(name, value):
        lines.append('probe {}: {} {}'.format(name, type(value).__name__, short(value)))
    def expect(ok, name, value, wanted):
        if not ok:
            raise MissingInput('QMT {} returned {}; expected {}'.format(name, short(value), wanted))
    bars = C.get_market_data_ex(BAR_FIELDS, [PROBE_STOCK, PROBE_INDEX], period='1d', end_time=end_time, count=3,
                                dividend_type='none', fill_data=False, subscribe=False)
    for code in (PROBE_STOCK, PROBE_INDEX):
        frame = (bars or {}).get(code)
        seen('get_market_data_ex ' + code, frame)
        expect(isinstance(frame, pd.DataFrame) and not frame.empty and set(BAR_FIELDS) <= set(frame.columns)
               and all(str(i)[:8].isdigit() for i in frame.index), 'get_market_data_ex', frame,
               'a DataFrame of {} indexed by YYYYMMDD'.format(BAR_FIELDS))
    factors = C.get_divid_factors(PROBE_STOCK)
    seen('get_divid_factors', factors)
    expect(isinstance(factors, dict) and factors, 'get_divid_factors', factors,
           '{epoch ms: [7 per-share values]} with past dividends')
    divid_events(factors)  # raises on a wrong row shape
    read = getattr(C, 'get_instrument_detail', None) or C.get_instrumentdetail
    detail = read(PROBE_STOCK)
    seen('get_instrument_detail', detail)
    expect(isinstance(detail, dict) and detail.get('InstrumentName') and 'UpStopPrice' in detail
           and 'DownStopPrice' in detail, 'get_instrument_detail', detail,
           'a dict with InstrumentName, UpStopPrice, DownStopPrice')
    expect(float(detail.get('TotalVolume') or 0) > 0, 'get_instrument_detail', detail,
           'TotalVolume, the total shares FINANCE(1) reads')
    for sector in sectors:
        codes = C.get_stock_list_in_sector(sector)
        seen('get_stock_list_in_sector ' + sector, len(codes or []))
        expect(codes and all(CODE.match(c) for c in codes), 'get_stock_list_in_sector ' + sector,
               (codes or [])[:5], "a non-empty list of codes like '600000.SH'")
    if live:
        tick = (C.get_full_tick([PROBE_STOCK]) or {}).get(PROBE_STOCK)
        seen('get_full_tick', tick)
        expect(isinstance(tick, dict) and all(k in tick for k in TICK_KEYS), 'get_full_tick', tick,
               'a dict with {}'.format(TICK_KEYS))
        lines.append('probe clock: machine {} latest tick {}'.format(clock, tick.get('timetag')))
    return lines

