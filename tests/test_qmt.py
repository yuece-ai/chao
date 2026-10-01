import io
from contextlib import redirect_stdout
import pandas as pd
import pytest
import chao.qmt_entry as entry
from chao.market import INDEX_SYMBOLS
from chao.qmt_source import HISTORY_BARS, QMT_INDEX, QmtMarket, ex_rights_from_divid, from_qmt, to_qmt
from chao.settings import ConfigError
from tests.qmt_fake import FakeAccount, FakeContextInfo, Obj, daily

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
    market = QmtMarket(C, ('A',), HISTORY_BARS)
    assert market.bars('SZ000001').close.tolist() == [9.5, 9.5]
    assert market.unadjusted_close('SZ000001', '2020-01-01') == 10.0
    assert set(market.index_closes()) == set(INDEX_SYMBOLS)
    assert market.universe() == ['SH600000', 'SZ000001']
    assert market.name('SZ000001') == 'PING AN' and market.name('SH600000') is None
    assert market.total_shares('SZ000001') == {'2019-01-01': 1e9}


def test_gui_beats_config_beats_default(monkeypatch):
    monkeypatch.setattr(entry, 'CONFIG', {'strategies': '1,2', 'sectors': 'A'})
    run, lines = entry.build_run(FakeContextInfo({}), FakeAccount().namespace(strategies=3, x=1))
    assert sorted(run.context.strategies) == [3] and run.context.settings.sectors == ('A',)
    assert "strategies = (3,)  (gui)" in lines and "sectors = ('A',)  (CONFIG)" in lines
    assert "priority = (6, 5, 4, 3, 2, 1)  (default)" in lines
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


def market_with(closes):
    return dict(INDEX_BARS, **{'000001.SZ': daily(closes), '000002.SZ': daily([10.0] * 10)})


def run_handlebar(C, account, **gui):
    with redirect_stdout(io.StringIO()) as out:
        C.chao, _ = entry.build_run(C, account.namespace(**gui))
        entry.handlebar(C)
    return out.getvalue().splitlines()


@pytest.fixture
def test_strategy(monkeypatch):
    monkeypatch.setattr(entry, 'strategy_files', lambda: TEST_STRATEGY)
    monkeypatch.setattr(entry, 'CONFIG', {'sectors': 'A', 'strategies': '1', 'trade_time': '00:00'})


def test_live_dry_run_prints_orders_without_placing(test_strategy):
    C = FakeContextInfo(market_with([10.0] * 299 + [11.0]), names={'000001.SZ': 'X'},
                        sectors={'A': ['000001.SZ', '000002.SZ', '000003.SZ']})
    account = FakeAccount(cash=100000.0)
    log = run_handlebar(C, account, account_id='A1', max_positions=2)
    assert 'chao: signal 2021-02-23 strategy=1 SZ000001 buy' in log
    # 50000 * 0.998 / 11 -> 4536 shares -> 4500 in whole lots.
    assert 'chao: order 2021-02-23 buy SZ000001 4500 strategy=1 (dry-run)' in log
    # 000002 has too little history and is ignored; 000003 has no bars and is reported.
    assert not any('SZ000002' in line for line in log)
    assert any(line.startswith('chao: skipped strategy=1 SZ000003: MissingInput') for line in log)
    assert account.orders == []


def test_live_without_account_only_reports_signals(test_strategy):
    C = FakeContextInfo(market_with([10.0] * 299 + [11.0]), names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    log = run_handlebar(C, FakeAccount())
    assert log[-2:] == ['chao: no account_id, so orders are not planned', 'chao: 1 signals, 0 skipped']


def test_live_orders_sell_only_owned_and_update_the_ledger(test_strategy, tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text('["SZ000001"]')
    C = FakeContextInfo(market_with([10.0] * 299 + [9.0]), names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(positions=[Obj(symbol='SZ000001', volume=1000, sellable=1000, value=9000.0),
                                     Obj(symbol='SZ000002', volume=500, sellable=500, value=5000.0)])
    run_handlebar(C, account, account_id='A1', dry_run=0, ledger_path=str(ledger))
    assert account.orders == [(24, 1101, 'A1', '000001.SZ', 5, 1000, 'chao', 2, 'chao-s1')]
    assert ledger.read_text() == '["SZ000001"]'  # still held until the sell fills


def test_backtest_places_each_bars_orders(test_strategy):
    closes = [10.0] * 298 + [11.0, 10.0]
    dates = pd.bdate_range('2020-01-01', periods=300).strftime('%Y%m%d')
    C = FakeContextInfo(market_with(closes), names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']},
                        backtest=True, bar_dates=dates)
    account = FakeAccount(cash=100000.0)
    with redirect_stdout(io.StringIO()):
        C.chao, _ = entry.build_run(C, account.namespace(account_id='testS'))
        for position in (298, 299):
            C.barpos = position
            entry.handlebar(C)
    # Bar 299 buys at 11.0 (quick 0 in backtests); bar 300 has a sell, but nothing is held yet.
    assert account.orders == [(23, 1101, 'testS', '000001.SZ', 5, 900, 'chao', 0, 'chao-s1')]
