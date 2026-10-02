from pathlib import Path
import datetime
import io
import json
from contextlib import redirect_stdout
import pandas as pd
import pytest
import chao.qmt_entry as entry
from chao.market import INDEX_SYMBOLS
from chao.qmt_source import HISTORY_BARS, QMT_INDEX, QmtMarket, ex_rights_from_divid, probe, from_qmt, to_qmt
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
                        names={'000001.SZ': 'PING AN'}, sectors={'A': ['000001.SZ', '600036.SH']},
                        shares={'000001.SZ': pd.Series([1e9], index=['20190101'])})
    market = QmtMarket(C, ('A',), HISTORY_BARS, '', '2020-01-02')
    market.prefetch(['SZ000001', 'SH600036'], ['999999', '399001'])
    assert C.calls == [('get_market_data_ex', 4)]  # one call for the batch and the needed indices
    assert market.bars('SZ000001').close.tolist() == [9.5, 9.5]
    assert set(market.index_closes()) == {'999999', '399001'}
    assert market.universe() == ['SH600036', 'SZ000001']
    assert market.name('SZ000001') == 'PING AN' and market.name('SH600036') is None
    with pytest.raises(ValueError, match='no daily bars from QMT for 600036.SH'):
        market.bars('SH600036')
    assert market.total_shares('SZ000001') == {'1990-01-01': 1e9}  # today's TotalVolume
    assert market.total_shares('SH600036') == {}  # no TotalVolume: FINANCE(1) is NaN, so no buy


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
    return dict(INDEX_BARS, **{'000001.SZ': daily(closes), '000002.SZ': daily([10.0] * 10, start='2021-02-10')})


def run_handlebar(C, account, **gui):
    with redirect_stdout(io.StringIO()) as out:
        entry.RUN, _ = entry.build_run(C, account.namespace(**gui))
        entry.handlebar(C)
    return out.getvalue().splitlines()


@pytest.fixture
def test_strategy(monkeypatch):
    monkeypatch.setattr(entry, 'strategy_files', lambda: TEST_STRATEGY)
    monkeypatch.setattr(entry, 'CONFIG', {'sectors': 'A', 'strategies': '1', 'prepare_time': '00:00', 'signal_time': '00:00',
                                          'order_time': '00:00', 'buy_time': '00:00'})
    monkeypatch.setattr(entry, 'now', lambda: datetime.datetime(2021, 2, 23, 14, 58))


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
    ledger.write_text('{"SZ000001": {"selling": false, "strategy": 1}}')
    C = live([10.0] * 299 + [9.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']},
             limits={'000001.SZ': (8.9, 10.9)})
    account = FakeAccount(positions=[Obj(symbol='SZ000001', volume=1000, sellable=1000, value=9000.0),
                                     Obj(symbol='SZ000002', volume=500, sellable=500, value=5000.0)])
    run_handlebar(C, account, account_id='A1', dry_run=0, ledger_path=str(ledger))
    # Limit 9 * 0.985 = 8.865 -> 8.87, inside the 跌停 floor of 8.90 -> 8.90.
    assert account.orders == [(24, 1101, 'A1', '000001.SZ', 11, 8.9, 1000, 'chao', 2, 'chao-s1')]
    # Still held until the sell fills, now marked as a pending sell.
    assert json.loads(ledger.read_text()) == {'SZ000001': {'selling': True, 'strategy': 1}}


def backtest_client(closes, **kwargs):
    dates = pd.bdate_range('2020-01-01', periods=len(closes)).strftime('%Y%m%d')
    return FakeContextInfo(market_with(closes), names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']},
                           backtest=True, bar_dates=dates, **kwargs)


def run_backtest(C, account, positions, **gui):
    with redirect_stdout(io.StringIO()) as out:
        entry.init_with(C, account.namespace(account_id='testS', **gui))
        for position in positions:
            C.barpos = position
            entry.handlebar(C)
    return out.getvalue().splitlines()


def test_backtest_mirrors_the_tdx_ledger_into_qmt(test_strategy, tmp_path):
    from chao.replay import ReplaySpec, replay
    closes = [10.0] * 297 + [11.0, 10.0, 10.0]
    C = backtest_client(closes, start='2021-02-01 00:00:00')
    account = FakeAccount()
    log = run_backtest(C, account, range(297, 300), report_path=str(tmp_path))
    assert C.capital == entry.BACKTEST_CAPITAL
    # The trades are exactly those of the TDX ledger for this stock.
    frame = market_with(closes)['000001.SZ']
    frame.index = pd.to_datetime(frame.index)
    from chao.signals import Signals
    sig = Signals(frame.index, (frame.close > frame.close.shift(1)).values, (frame.close < frame.close.shift(1)).values)
    events, _ = replay(frame, sig, ReplaySpec('2021-02-01', '2021-02-23', 1e6, 0.0005, 0.0003))
    assert [e['direction'] for e in events] == ['买开', '卖平']
    shares = events[0]['quantity']
    assert account.orders == [(23, 1101, 'testS', '000001.SZ', 5, -1, shares, 'chao', 2, 'chao-s1'),
                              (24, 1101, 'testS', '000001.SZ', 5, -1, shares, 'chao', 2, 'chao-s1')]
    from chao.tdx_report import latest_exports
    lines = Path(latest_exports(str(tmp_path))[1]).read_text(encoding='utf-8').splitlines()
    assert lines[1].split('\t')[:6] == ['000001', 'X', '2021-02-19 00:00', '买开', '11.00', str(shares)]
    assert any(line.startswith('chao: tdx strategy=1 trades=1 wins=0') for line in log)


def test_backtest_warns_where_tdx_buys_and_sells_on_one_bar(test_strategy, monkeypatch):
    same_bar = {'1-test.tdx': '{策略1-测试}\n买入条件:=CLOSE>REF(CLOSE,1);\n卖出条件:=CLOSE>REF(CLOSE,1);\n'}
    monkeypatch.setattr(entry, 'strategy_files', lambda: same_bar)
    C = backtest_client([10.0] * 298 + [11.0, 11.0], start='2021-02-01 00:00:00')
    log = run_backtest(C, FakeAccount(), [298])
    assert any('SZ000001 is bought and sold on the same bar in TDX; QMT applies T+1' in line for line in log)


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
    assert any(' 1 without a tick today ' in line for line in log)


def test_live_stops_when_indices_have_no_bar_today(test_strategy):
    bars = market_with([10.0] * 299 + [11.0])
    yesterday = {code: frame.iloc[:-1] for code, frame in bars.items()}
    C = FakeContextInfo(yesterday, names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(cash=100000.0)
    log = run_handlebar(C, account, account_id='A1')
    assert any(line.startswith('chao: error in live_signals: MissingInput: indices without a tick for 2021-02-23')
               for line in log)
    entry.handlebar(C)  # the next tick does not retry the failed day
    assert not any(line.startswith('chao: order') for line in log)
    assert C.calls.count(('get_full_tick', 5)) == 1


def test_live_stages_run_at_their_own_times(test_strategy, monkeypatch, tmp_path):
    monkeypatch.setattr(entry, 'CONFIG', dict(entry.CONFIG, signal_time='14:56:00', order_time='14:56:45',
                                              buy_time='14:56:50'))
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(cash=100000.0)
    clock = {'now': '14:55:59'}
    monkeypatch.setattr(entry, 'now', lambda: datetime.datetime.strptime('2021-02-23 ' + clock['now'], '%Y-%m-%d %H:%M:%S'))
    with redirect_stdout(io.StringIO()) as out:
        entry.init_with(C, account.namespace(account_id='A1', dry_run=0, ledger_path=str(tmp_path / 'l.json')))
    seen = {}
    for clock['now'] in ('14:55:59', '14:56:00', '14:56:30', '14:56:45', '14:56:48', '14:56:50', '14:56:53'):
        with redirect_stdout(out):
            entry.handlebar(C)
        seen[clock['now']] = list(account.orders)
    assert seen['14:56:48'] == [] and '2021-02-23' in entry.RUN.signals   # signals, no buy before buy_time
    assert [o[0] for o in seen['14:56:50']] == [23]                       # the buy goes out at buy_time
    assert [o[0] for o in account.orders] == [23]                         # and only once
    assert C.calls.count(('get_market_data_ex', 5)) == 1                  # signals computed once


def test_live_does_not_trade_on_a_day_without_its_bar(test_strategy, monkeypatch):
    monkeypatch.setattr(entry, 'now', lambda: datetime.datetime(2021, 2, 27, 14, 58))  # a Saturday
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(cash=100000.0)
    log = run_handlebar(C, account, account_id='A1')
    assert log[-1] == 'chao: the main chart has no bar for 2021-02-27 (last 2021-02-23); not trading yet'
    assert account.orders == [] and C.calls == []


def test_live_sends_nothing_after_the_close(test_strategy, monkeypatch, tmp_path):
    monkeypatch.setattr(entry, 'now', lambda: datetime.datetime(2021, 2, 23, 15, 30))
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(cash=100000.0)
    log = run_handlebar(C, account, account_id='A1', dry_run=0, ledger_path=str(tmp_path / 'l.json'))
    assert log[-1] == 'chao: 2021-02-23 started after 15:00:00; no orders today'
    assert account.orders == []


def test_a_failed_backtest_preparation_never_trades(test_strategy):
    C = backtest_client([10.0] * 297 + [11.0, 10.0, 10.0], start='2021-02-01 00:00:00', end='2021-03-31 15:00:00')
    for code in QMT_INDEX.values():  # the chart reaches 2021-02-23, the downloaded index bars do not
        C.bars[code] = C.bars[code].iloc[:-1]
    account = FakeAccount()
    with pytest.raises(ValueError, match='QMT index bars end on 2021-02-22, before the last trading day 2021-02-23'):
        run_backtest(C, account, [297])
    entry.handlebar(C)  # later bars: stopped, no retry and no partial trades
    assert account.orders == [] and C.calls.count(('get_market_data_ex', 5)) == 1


def test_startup_reports_missing_qmt_calls():
    class Bare(FakeContextInfo):
        get_full_tick = None
    with pytest.raises(ConfigError, match="QMT API not available: \\['get_full_tick'\\]"):
        entry.init_with(Bare({}), FakeAccount().namespace())


def test_older_clients_name_the_detail_call_get_instrumentdetail():
    class OldClient(FakeContextInfo):
        get_instrument_detail = None
        def get_instrumentdetail(self, code):
            return {'InstrumentName': 'OLD'}
    market = QmtMarket(OldClient({}), (), HISTORY_BARS, '', '2021-02-23')
    assert market.name('SZ000001') == 'OLD'


def ledger_with(tmp_path, entries):
    path = tmp_path / 'ledger.json'
    path.write_text(json.dumps(entries))
    return str(path)


def test_a_pending_sell_is_retried_without_a_new_signal(test_strategy, tmp_path):
    # Today's close is up: strategy 1 signals no sell, but yesterday's sell is still pending.
    C = live([10.0] * 299 + [11.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(positions=[Obj(symbol='SZ000001', volume=1000, sellable=1000, value=11000.0)])
    path = ledger_with(tmp_path, {'SZ000001': {'strategy': 1, 'selling': True}})
    run_handlebar(C, account, account_id='A1', dry_run=0, ledger_path=path)
    assert [(o[0], o[6]) for o in account.orders] == [(24, 1000)]


def test_live_without_ready_data_only_sends_pending_sells(test_strategy, tmp_path):
    # The local index bars stop two days back: no new buys today, the pending sell still goes out.
    bars = market_with([10.0] * 299 + [11.0])
    stale = dict(bars, **{code: bars[code].iloc[:-2] for code in QMT_INDEX.values()})
    C = FakeContextInfo(stale, ticks=ticks_from(bars, LAST), names={'000001.SZ': 'X', '000002.SZ': 'Y'},
                        sectors={'A': ['000001.SZ', '000002.SZ']})
    account = FakeAccount(cash=100000.0, positions=[Obj(symbol='SZ000002', volume=500, sellable=500, value=5000.0)])
    path = ledger_with(tmp_path, {'SZ000002': {'strategy': 1, 'selling': True}})
    log = run_handlebar(C, account, account_id='A1', dry_run=0, ledger_path=path)
    assert 'chao: data not ready for 2021-02-23; no new trades today, pending sells still go out' in log
    assert [(o[0], o[3], o[6]) for o in account.orders] == [(24, '000002.SZ', 500)]


def test_live_requests_the_daily_download_on_the_first_tick(test_strategy):
    C = live([10.0] * 300, names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount()
    run_handlebar(C, account)
    entry.handlebar(C)
    indices = [QMT_INDEX[code] for code in sorted(entry.RUN.index_codes)]
    assert indices and account.downloads == [(code, '1d', '', '') for code in ['000001.SZ'] + indices]  # once


def test_only_the_buying_strategy_sells(test_strategy, tmp_path, monkeypatch):
    # The stock was bought by strategy 2; strategy 1's sell signal does not sell it.
    monkeypatch.setattr(entry, 'CONFIG', dict(entry.CONFIG, strategies='1,2', priority='2,1'))
    monkeypatch.setattr(entry, 'strategy_files', lambda: dict(TEST_STRATEGY, **{
        '2-hold.tdx': '{策略2-持有}\n买入条件:=CLOSE<0;\n卖出条件:=CLOSE<0;\n'}))
    C = live([10.0] * 299 + [9.0], names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ']})
    account = FakeAccount(positions=[Obj(symbol='SZ000001', volume=1000, sellable=1000, value=9000.0)])
    path = ledger_with(tmp_path, {'SZ000001': {'strategy': 2, 'selling': False}})
    log = run_handlebar(C, account, account_id='A1', dry_run=0, ledger_path=path)
    assert 'chao: signal 2021-02-23 strategy=1 SZ000001 sell' in log
    assert account.orders == []


def test_startup_probe_shows_an_unexpected_shape():
    C = FakeContextInfo({})
    C.divid['600000.SH'] = {1699200000000: [0.4, 0.0]}  # five values missing
    entry.init_with(C, FakeAccount().namespace())
    with pytest.raises(ValueError, match=r'unexpected get_divid_factors row \[0.4, 0.0\]'):
        entry.handlebar(C)


def test_price_margin_must_stay_inside_the_price_cage():
    with pytest.raises(ConfigError, match='price_margin must be in'):
        entry.build_run(FakeContextInfo({}), FakeAccount().namespace(price_margin=0.03))


def test_total_shares_come_from_the_contract_details():
    C = FakeContextInfo({}, backtest=True)
    C.shares['600000.SH'] = pd.Series([float('nan'), 3e10], index=['20200101', '20200102'])
    probe(C, (), '20200102', False, '2021-02-23 14:00:00')  # the fake has no get_financial_data
    C.shares['600000.SH'] = pd.Series([float('nan')], index=['20200101'])
    with pytest.raises(ValueError, match='TotalVolume, the total shares FINANCE'):
        probe(C, (), '20200102', False, '2021-02-23 14:00:00')


def test_mode_is_decided_at_the_first_bar(test_strategy):
    # The client reported do_back_test=False in init for a backtest.
    C = backtest_client([10.0] * 297 + [11.0, 10.0, 10.0], start='2021-02-01 00:00:00')
    C.do_back_test = False
    with redirect_stdout(io.StringIO()) as out:
        entry.init_with(C, FakeAccount().namespace(account_id='testS'))
        C.do_back_test, C.barpos = True, 299
        entry.handlebar(C)
    assert entry.RUN.backtest
    assert 'chao: mode backtest (do_back_test=True at the first bar)' in out.getvalue()
    assert 'warning: the backtest capital was not raised in init' in out.getvalue()


def test_mode_can_be_forced(test_strategy):
    C = backtest_client([10.0] * 297 + [11.0, 10.0, 10.0], start='2021-02-01 00:00:00')
    C.do_back_test = False
    with redirect_stdout(io.StringIO()):
        entry.init_with(C, FakeAccount().namespace(account_id='testS', mode='backtest'))
        C.barpos = 299
        entry.handlebar(C)
    assert entry.RUN.backtest and C.capital == entry.BACKTEST_CAPITAL



def test_tick_amount_unit_is_read_from_the_tick():
    from chao.qmt_source import tick_amount_unit, with_tick
    # 600000.SH on 2026-09-30 as the client reported it: 1.386e9 yuan traded, amount says 1.386e11.
    seen = {'timetag': '20260930 15:30:07', 'lastPrice': 9.48, 'open': 9.22, 'high': 9.49, 'low': 9.16,
            'amount': 138620993700.0, 'pvolume': 147484820}
    assert tick_amount_unit(seen) == 100
    assert tick_amount_unit(dict(seen, amount=1386209900.0)) == 1  # the same day's tick, read again
    assert tick_amount_unit(dict(seen, amount=1e6)) is None
    assert tick_amount_unit(dict(seen, amount=0.0, pvolume=0)) == 1  # nothing traded
    bar = with_tick(None, seen, pd.Timestamp('2026-09-30'), 100)
    assert bar['amount'].tolist() == [1386209937.0]


def test_live_orders_check_the_ledger_is_writable_at_startup(tmp_path):
    with pytest.raises(OSError):
        entry.build_run(FakeContextInfo({}), FakeAccount().namespace(
            account_id='A1', dry_run=0, ledger_path=str(tmp_path / 'missing' / 'ledger.json')))
    entry.build_run(FakeContextInfo({}), FakeAccount().namespace(
        account_id='A1', dry_run=0, ledger_path=str(tmp_path / 'ledger.json')))
    assert (tmp_path / 'ledger.json').read_text() == '{}'
    (tmp_path / 'ledger.json').write_text('{"SH600000": {"selling": false, "strategy": 1}}')
    entry.build_run(FakeContextInfo({}), FakeAccount().namespace(account_id='A1', ledger_path=str(tmp_path / 'ledger.json')))
    assert '"SH600000"' in (tmp_path / 'ledger.json').read_text()  # a dry run checks too and keeps the entries


def test_backtest_preparation_reports_progress(test_strategy):
    log = run_backtest(backtest_client([10.0] * 300, start='2021-02-01 00:00:00'), FakeAccount(), [299])
    assert any(l.startswith('chao: backtest preparing 1 stocks 2021-02-01 .. ') for l in log)
    line = next(l for l in log if l.startswith('chao: backtest preparing 1/1 stocks, '))  # any elapsed time
    assert 'get_market_data_ex' in line  # time per QMT data call


def test_a_backtest_may_end_on_a_weekend(test_strategy):
    # 2024-06-30, the integrator's end, was a Sunday; here 2021-02-28 is one.
    C = backtest_client([10.0] * 297 + [11.0, 10.0, 10.0], start='2021-02-01 00:00:00', end='2021-02-28 15:00:00')
    log = run_backtest(C, FakeAccount(), [297])
    assert any(line.startswith('chao: backtest ready') for line in log)
    assert entry.last_chart_day(C, '2021-02-28') == '2021-02-23' and entry.last_chart_day(C, '2020-01-01') == '2020-01-01'


def test_a_forbidden_report_folder_does_not_stop_the_backtest(test_strategy, monkeypatch):
    def forbidden(*args):
        raise PermissionError('Foribdden FileIO')
    monkeypatch.setattr(entry, 'write_exports', forbidden)
    C = backtest_client([10.0] * 297 + [11.0, 10.0, 10.0], start='2021-02-01 00:00:00')
    log = run_backtest(C, FakeAccount(), [297], report_path='D:\\chao\\report')
    assert any('cannot write the trade lists' in line for line in log)
    assert any(line.startswith('chao: backtest ready') for line in log)


def test_backtest_bars_are_adjusted_as_of_today():
    # The backtest ends 2020-01-01; a 0.5 cash dividend goes ex on 2020-01-02.
    bars = dict(INDEX_BARS, **{'000001.SZ': daily([10.0, 9.5], start='2020-01-01')})
    C = FakeContextInfo(bars, divid={'000001.SZ': {'20200102': [0.5, 0, 0, 0, 0, 0, 1.0]}}, backtest=True)
    market = QmtMarket(C, (), 10, '20200101', '2020-01-02')
    market.prefetch(['SZ000001'], ['999999'])
    assert market.bars('SZ000001').close.tolist() == [9.5]
    # The data ends 2020-01-01: an event announced for later does not apply yet.
    market = QmtMarket(C, (), 10, '20200101', '2020-01-01')
    market.prefetch(['SZ000001'], ['999999'])
    assert market.bars('SZ000001').close.tolist() == [10.0]


def test_each_run_writes_new_report_files_and_readers_take_the_newest(tmp_path):
    from chao.tdx_report import latest_exports, write_exports
    write_exports(str(tmp_path), {3: []}, {}, '20261002-100000')
    write_exports(str(tmp_path), {3: []}, {}, '20261002-110000')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['strategy-3-signals-20261002-100000.tsv',
                                                          'strategy-3-signals-20261002-110000.tsv']
    assert latest_exports(str(tmp_path)) == {3: str(tmp_path / 'strategy-3-signals-20261002-110000.tsv')}
