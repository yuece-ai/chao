"""A stand-in for QMT's ContextInfo, returning data in QMT's documented shapes."""
import pandas as pd


class FakeContextInfo:
    def __init__(self, bars, divid=None, names=None, shares=None, sectors=None, last_bar=True,
                 backtest=False, bar_dates=('20210223',)):
        self.bars = bars            # {qmt code: DataFrame indexed by 'YYYYMMDD'}
        self.divid = divid or {}    # {qmt code: {'YYYYMMDD': [7 per-share values]}}
        self.names = names or {}
        self.shares = shares or {}  # {qmt code: Series indexed by 'YYYYMMDD'}
        self.sectors = sectors or {}
        self.last_bar = last_bar
        self.do_back_test = backtest
        self.bar_dates = list(bar_dates)  # one 'YYYYMMDD' per bar position
        self.barpos = len(self.bar_dates) - 1

    def get_market_data_ex(self, fields, stock_code, period, count, dividend_type, fill_data, subscribe):
        assert period == '1d' and dividend_type == 'none' and not subscribe
        return {code: self.bars[code][fields] if count == -1 else self.bars[code][fields].tail(count)
                for code in stock_code if code in self.bars}

    def get_divid_factors(self, code):
        return self.divid.get(code, {})

    def get_stock_name(self, code):
        return self.names.get(code, '')

    def get_financial_data(self, fields, codes, start, end, report_type):
        assert fields == ['CAPITALSTRUCTURE.total_capital'] and report_type == 'announce_time'
        return self.shares.get(codes[0], pd.Series(dtype=float))

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

    def get_trade_detail_data(self, account_id, account_type, kind):
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
