import io
from contextlib import redirect_stdout
import pandas as pd
import pytest
import chao.qmt_entry as entry
from chao.market import INDEX_SYMBOLS
from chao.qmt_source import HISTORY_BARS, QMT_INDEX, QmtMarket, ex_rights_from_divid, from_qmt, to_qmt
from chao.settings import ConfigError
from tests.qmt_fake import FakeAccount, FakeContextInfo, Obj, daily, ticks_from

INDEX_BARS = {code: daily([1000.0] * 300) for code in QMT_INDEX.values()}
# A tiny strategy with predictable signals: buy on an up close, sell on a down close.
TEST_STRATEGY = {'1-test.tdx': '{策略1-测试}\n买入条件:=CLOSE>REF(CLOSE,1);\n卖出条件:=CLOSE<REF(CLOSE,1);\n'}
LAST = '20210223'  # date of bar 300 from daily()


def test_symbol_round_trip():
    assert to_qmt('SH600000') == '600000.SH' and from_qmt('920580.BJ') == 'BJ920580'
    assert set(QMT_INDEX) == set(INDEX_SYMBOLS)


def test_divid_factors_become_per_ten_ex_rights_up_to_as_of():
    factors = {'20200102': [0.18, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0], '20200110': [0.5, 0, 0, 0, 0, 0, 1.0]}
    (event,) = ex_rights_from_divid(factors, '2020-01-05')
    assert (float(event.cash), float(event.bonus), event.date) == (1.8, 3.0, pd.Timestamp('2020-01-02'))
    with pytest.raises(ValueError, match='unexpected get_divid_factors row'):
        ex_rights_from_divid({'20200102': [0.18]}, '2020-01-05')


def test_qmt_market_adjusts_bars_and_keeps_traded_closes():
    bars = dict(INDEX_BARS, **{'000001.SZ': daily([10.0, 9.5], start='2020-01-01')})
    C = FakeContextInfo(bars, divid={'000001.SZ': {'20200102': [0.5, 0, 0, 0, 0, 0, 1.0]}},
                        names={'000001.SZ': 'PING AN'}, sectors={'A': ['000001.SZ', '600000.SH']},
                        shares={'000001.SZ': pd.Series([1e9], index=['20190101'])})
    market = QmtMarket(C, ('A',), HISTORY_BARS, '')
    market.prefetch(['SZ000001', 'SH600000'], ['999999', '399001'])
    assert C.calls == [('get_market_data_ex', 4)]  # one call for the batch and the needed indices
    assert market.bars('SZ000001').close.tolist() == [9.5, 9.5]
    assert market.unadjusted_close('SZ000001', '2020-01-01') == 10.0
    assert set(market.index_closes()) == {'999999', '399001'}
    assert market.universe() == ['SH600000', 'SZ000001']
    assert market.name('SZ000001') == 'PING AN' and market.name('SH600000') is None
    with pytest.raises(ValueError, match='no daily bars from QMT for 600000.SH'):
        market.bars('SH600000')
    assert market.total_shares('SZ000001') == {'2019-01-01': 1e9}


def test_gui_beats_config_beats_default(monkeypatch):
    monkeypatch.setattr(entry, 'CONFIG', {'strategies': '1,2', 'sectors': 'A'})
    run, lines = entry.build_run(FakeContextInfo({}), FakeAccount().namespace(strategies=3, x=1))
    assert sorted(run.context.strategies) == [3] and run.context.settings.sectors == ('A',)
    assert "strategies = (3,)  (gui)" in lines and "sectors = ('A',)  (CONFIG)" in lines
    assert "priority = (6, 5, 4, 3, 2, 1)  (default)" in lines
    run, lines = entry.build_run(FakeContextInfo({}), FakeAccount().namespace(account='ACC'))
    assert run.context.settings.account_id == 'ACC' and "account_id = 'ACC'  (qmt account)" in lines
    monkeypatch.setattr(entry, 'CONFIG', {'sector': 'A'})
    with pytest.raises(ConfigError, match='CONFIG: unknown keys'):
        entry.build_run(FakeContextInfo({}), FakeAccount().namespace())


@pytest.mark.parametrize('backtest,gui,message', [
    (True, {}, 'account_id is required'),
    (False, {'account_id': 'A1', 'dry_run': 0}, 'ledger_path is required'),
    (False, {'strategies': '1,7', 'priority': '1'}, r'priority must list every strategy run; missing \[7\]'),
])
def test_unsafe_trading_setups_are_rejected(backtest, gui, message):
    with pytest.raises(ConfigError, match=message):
        entry.build_run(FakeContextInfo({}, backtest=backtest), FakeAccount().namespace(**gui))


def test_main_chart_must_be_daily():
    C = FakeContextInfo({})
    C.period = '5m'
    with pytest.raises(ConfigError, match='main chart must be daily'):
        entry.build_run(C, FakeAccount().namespace())


def market_with(closes):
    return dict(INDEX_BARS, **{'000001.SZ': daily(closes), '000002.SZ': daily([10.0] * 10)})


def run_handlebar(C, account, **gui):
    with redirect_stdout(io.StringIO()) as out:
        entry.RUN, _ = entry.build_run(C, account.namespace(**gui))
        entry.handlebar(C)
    return out.getvalue().splitlines()


@pytest.fixture
def test_strategy(monkeypatch):
    monkeypatch.setattr(entry, 'strategy_files', lambda: TEST_STRATEGY)
    monkeypatch.setattr(entry, 'CONFIG', {'sectors': 'A', 'strategies': '1', 'signal_time': '00:00',
                                          'order_time': '00:00'})


def live(closes, **kwargs):
    """A live client whose local bars include today and whose ticks repeat today's bar."""
    bars = market_with(closes)
    return FakeContextInfo(bars, ticks=ticks_from(bars, LAST), **kwargs)


def test_live_dry_run_prints_orders_without_placing(test_strategy):
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'},
             sectors={'A': ['000001.SZ', '000002.SZ', '000003.SZ']})
    account = FakeAccount(cash=100000.0)
    log = run_handlebar(C, account, account_id='A1', max_positions=2)
    assert 'chao: signal 2021-02-23 strategy=1 SZ000001 buy' in log
    # Limit 11 * 1.015 -> 11.17; 50000 * 0.998 / 11.17 -> 4467 shares -> 4400 in whole lots.
    assert 'chao: order 2021-02-23 buy SZ000001 4400 strategy=1 limit=11.17 (dry-run)' in log
    # 000002 has too little history and is ignored; 000003 has no bars and is reported.
    assert not any('SZ000002' in line for line in log)
    assert any(line.startswith('chao: skipped strategy=1 SZ000003: MissingInput') for line in log)
    assert account.orders == []


def test_live_without_account_only_reports_signals(test_strategy):
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    log = run_handlebar(C, FakeAccount())
    assert log[-2].startswith('chao: 1 signals, 0 skipped, ')
    assert log[-1] == 'chao: no account_id, so orders are not planned'


def test_live_orders_sell_only_owned_and_update_the_ledger(test_strategy, tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text('["SZ000001"]')
    C = live([10.0] * 299 + [9.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']},
             limits={'000001.SZ': (8.9, 10.9)})
    account = FakeAccount(positions=[Obj(symbol='SZ000001', volume=1000, sellable=1000, value=9000.0),
                                     Obj(symbol='SZ000002', volume=500, sellable=500, value=5000.0)])
    run_handlebar(C, account, account_id='A1', dry_run=0, ledger_path=str(ledger))
    # Limit 9 * 0.985 = 8.865 -> 8.87, inside the 跌停 floor of 8.90 -> 8.90.
    assert account.orders == [(24, 1101, 'A1', '000001.SZ', 11, 8.9, 1000, 'chao', 2, 'chao-s1')]
    assert ledger.read_text() == '["SZ000001"]'  # still held until the sell fills


def test_backtest_places_each_bars_orders(test_strategy):
    closes = [10.0] * 298 + [11.0, 10.0]
    dates = pd.bdate_range('2020-01-01', periods=300).strftime('%Y%m%d')
    C = FakeContextInfo(market_with(closes), names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']},
                        backtest=True, bar_dates=dates)
    account = FakeAccount(cash=100000.0)
    with redirect_stdout(io.StringIO()):
        entry.RUN, _ = entry.build_run(C, account.namespace(account_id='testS'))
        for position in (298, 299):
            C.barpos = position
            entry.handlebar(C)
    # Bar 299 buys at 11.0 on its own bar (quickTrade 2); bar 300 has a sell,
    # but the fake account holds nothing yet.
    assert account.orders == [(23, 1101, 'testS', '000001.SZ', 5, -1, 900, 'chao', 2, 'chao-s1')]
    # One batch plus the four SH/SZ board indices, read once for the whole backtest.
    assert C.calls == [('get_market_data_ex', 5)]


def test_backtest_sells_owned_stocks_from_the_sell_table(test_strategy):
    closes = [10.0] * 298 + [11.0, 10.0]
    dates = pd.bdate_range('2020-01-01', periods=300).strftime('%Y%m%d')
    C = FakeContextInfo(market_with(closes), names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']},
                        backtest=True, bar_dates=dates, start='2021-02-23 00:00:00')
    account = FakeAccount(positions=[Obj(symbol='SZ000001', volume=900, sellable=900, value=9900.0)])
    with redirect_stdout(io.StringIO()) as out:
        entry.RUN, _ = entry.build_run(C, account.namespace(account_id='testS'))
        entry.RUN.ledger.replace({'SZ000001'})
        C.barpos = 299
        entry.handlebar(C)
    assert account.orders == [(24, 1101, 'testS', '000001.SZ', 5, -1, 900, 'chao', 2, 'chao-s1')]
    # Only the window from C.start is kept: the 2021-02-22 buy falls outside it.
    assert 'chao: backtest signals ready: 0 buy rows, 1 stocks with sells, 0 skipped' in out.getvalue()


def test_restarted_live_run_does_not_repeat_todays_buys(test_strategy, tmp_path):
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(cash=100000.0)  # the buy stays unfilled: no position appears
    gui = dict(account_id='A1', dry_run=0, ledger_path=str(tmp_path / 'ledger.json'))
    with redirect_stdout(io.StringIO()):
        for _ in range(2):  # the second build is a strategy restart on the same day
            entry.RUN, _ = entry.build_run(C, account.namespace(**gui))
            entry.handlebar(C)
    assert [o[0] for o in account.orders] == [23]


def test_live_takes_todays_bar_from_ticks(test_strategy):
    # Local daily bars (subscribe=False) end yesterday; today's bar comes from get_full_tick.
    bars = market_with([10.0] * 299 + [11.0])
    yesterday = {code: frame.iloc[:-1] for code, frame in bars.items()}
    def tick(close):
        return {'timetag': '20210223 14:50:00', 'lastPrice': close, 'open': close, 'high': close,
                'low': close, 'amount': 1e8}
    ticks = dict({code: tick(1000.0) for code in QMT_INDEX.values()}, **{'000001.SZ': tick(11.0)})
    C = FakeContextInfo(yesterday, names={'000001.SZ': 'X', '000002.SZ': 'Y'},
                        sectors={'A': ['000001.SZ', '000002.SZ']}, ticks=ticks)
    log = run_handlebar(C, FakeAccount(cash=100000.0), account_id='A1', max_positions=2)
    assert 'chao: signal 2021-02-23 strategy=1 SZ000001 buy' in log
    assert 'chao: order 2021-02-23 buy SZ000001 4400 strategy=1 limit=11.17 (dry-run)' in log
    # 000002 has no tick today (suspended): it gets no bar for today and is counted.
    assert any(' 1 without a bar today ' in line for line in log)


def test_live_stops_when_indices_have_no_bar_today(test_strategy):
    bars = market_with([10.0] * 299 + [11.0])
    yesterday = {code: frame.iloc[:-1] for code, frame in bars.items()}
    C = FakeContextInfo(yesterday, names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount()
    with pytest.raises(ValueError, match='indices without a bar for 2021-02-23'):
        run_handlebar(C, account, account_id='A1')
    entry.handlebar(C)  # the next tick does not retry the failed day
    assert C.calls.count(('get_full_tick', 5)) == 1


def test_live_signals_and_orders_happen_at_their_own_times(test_strategy, monkeypatch, tmp_path):
    monkeypatch.setattr(entry, 'CONFIG', dict(entry.CONFIG, signal_time='14:56:00', order_time='14:56:45'))
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(cash=100000.0)
    clock = {'now': '14:55:59'}
    monkeypatch.setattr(entry, 'now_hms', lambda: clock['now'])
    with redirect_stdout(io.StringIO()) as out:
        entry.RUN, _ = entry.build_run(C, account.namespace(account_id='A1', dry_run=0,
                                                           ledger_path=str(tmp_path / 'ledger.json')))
    for clock['now'] in ('14:55:59', '14:56:00', '14:56:30', '14:56:45', '14:56:48'):
        with redirect_stdout(out):
            entry.handlebar(C)
        if clock['now'] == '14:56:30':
            assert account.orders == [] and '2021-02-23' in entry.RUN.signals
    assert [o[0] for o in account.orders] == [23]  # sent once, at 14:56:45
    assert C.calls.count(('get_market_data_ex', 5)) == 1  # signals computed once


def test_older_clients_name_the_detail_call_get_instrumentdetail():
    class OldClient(FakeContextInfo):
        get_instrument_detail = None
        def get_instrumentdetail(self, code):
            return {'InstrumentName': 'OLD'}
    market = QmtMarket(OldClient({}), (), HISTORY_BARS, '')
    assert market.name('SZ000001') == 'OLD'
