"""A stand-in for QMT's ContextInfo, returning data in QMT's documented shapes."""
import pandas as pd


class FakeContextInfo:
    def __init__(self, bars, divid=None, names=None, shares=None, sectors=None, last_bar=True,
                 backtest=False, bar_dates=('20210223',), start='2020-01-01 00:00:00',
                 end='2021-02-23 15:00:00'):
        self.bars = bars            # {qmt code: DataFrame indexed by 'YYYYMMDD'}
        self.divid = divid or {}    # {qmt code: {'YYYYMMDD': [7 per-share values]}}
        self.names = names or {}
        self.shares = shares or {}  # {qmt code: Series indexed by 'YYYYMMDD'}
        self.sectors = sectors or {}
        self.last_bar = last_bar
        self.do_back_test = backtest
        self.period = '1d'
        self.start, self.end = start, end
        self.calls = []                   # (function, number of codes) per data call
        self.bar_dates = list(bar_dates)  # one 'YYYYMMDD' per bar position
        self.barpos = len(self.bar_dates) - 1

    def get_market_data_ex(self, fields, stock_code, period, end_time, count, dividend_type, fill_data, subscribe):
        assert period == '1d' and dividend_type == 'none' and not subscribe and not fill_data
        self.calls.append(('get_market_data_ex', len(stock_code)))
        result = {}
        for code in stock_code:
            if code in self.bars:
                frame = self.bars[code][fields]
                frame = frame[frame.index <= end_time] if end_time else frame
                result[code] = frame if count == -1 else frame.tail(count)
        return result

    def get_divid_factors(self, code):
        return self.divid.get(code, {})

    def get_instrumentdetail(self, code):
        return {'InstrumentID': code[:6], 'InstrumentName': self.names.get(code, '')}

    def get_financial_data(self, fields, codes, start, end, report_type):
        # One stock over a date range: DataFrame indexed by date, one column per field.
        assert fields == ['CAPITALSTRUCTURE.total_capital'] and report_type == 'announce_time'
        shares = self.shares.get(codes[0], pd.Series(dtype=float))
        return pd.DataFrame({fields[0]: shares})

    def get_stock_list_in_sector(self, sector):
        return self.sectors.get(sector, [])

    def is_last_bar(self):
        return self.last_bar

    def get_bar_timetag(self, position):
        return self.bar_dates[position]


class Obj:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class FakeAccount:
    """passorder/get_trade_detail_data stand-ins; records every order."""

    def __init__(self, cash=1e6, positions=()):
        self.cash, self.positions, self.orders = cash, list(positions), []

    def get_trade_detail_data(self, account_id, account_type, kind, strategy_name=None):
        assert account_type == 'STOCK'
        if kind == 'ORDER':
            return [Obj(m_strExchangeID=o[3][-2:], m_strInstrumentID=o[3][:6])
                    for o in self.orders if o[6] == strategy_name]
        if kind == 'ACCOUNT':
            return [Obj(m_dAvailable=self.cash, m_dBalance=self.cash + sum(p.value for p in self.positions))]
        return [Obj(m_strExchangeID=p.symbol[:2], m_strInstrumentID=p.symbol[2:], m_nVolume=p.volume,
                    m_nCanUseVolume=p.sellable) for p in self.positions]

    def passorder(self, op, order_type, account_id, code, pr_type, price, volume, name, quick, remark, C):
        self.orders.append((op, order_type, account_id, code, pr_type, volume, name, quick, remark))

    def namespace(self, **gui):
        return dict(gui, passorder=self.passorder, get_trade_detail_data=self.get_trade_detail_data)


def daily(closes, start='2020-01-01'):
    dates = pd.bdate_range(start, periods=len(closes)).strftime('%Y%m%d')
    return pd.DataFrame({'open': closes, 'high': closes, 'low': closes, 'close': closes,
                         'amount': [1e8] * len(closes)}, index=dates)
