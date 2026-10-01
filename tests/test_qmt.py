import io
from contextlib import redirect_stdout
import pandas as pd
import pytest
import chao.qmt_entry as entry
from chao.market import INDEX_SYMBOLS
from chao.qmt_source import QMT_INDEX, QmtMarket, ex_rights_from_divid, from_qmt, to_qmt
from chao.settings import ConfigError
from tests.qmt_fake import FakeContextInfo, daily

INDEX_BARS = {code: daily([1000.0] * 300) for code in QMT_INDEX.values()}


def test_symbol_round_trip():
    assert to_qmt('SH600000') == '600000.SH' and from_qmt('920580.BJ') == 'BJ920580'
    assert set(QMT_INDEX) == set(INDEX_SYMBOLS)


def test_divid_factors_become_per_ten_ex_rights_up_to_as_of():
    factors = {'20200102': [0.18, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0], '20200110': [0.5, 0, 0, 0, 0, 0, 1.0]}
    (event,) = ex_rights_from_divid(factors, '2020-01-05')
    assert (float(event.cash), float(event.bonus), event.date) == (1.8, 3.0, pd.Timestamp('2020-01-02'))
    with pytest.raises(ValueError, match='unexpected get_divid_factors row'):
        ex_rights_from_divid({'20200102': [0.18]}, '2020-01-05')


def test_qmt_market_adjusts_bars_and_maps_indices():
    bars = dict(INDEX_BARS, **{'000001.SZ': daily([10.0, 9.5], start='2020-01-01')})
    C = FakeContextInfo(bars, divid={'000001.SZ': {'20200102': [0.5, 0, 0, 0, 0, 0, 1.0]}},
                        names={'000001.SZ': 'PING AN'}, sectors={'A': ['000001.SZ', '600000.SH']},
                        shares={'000001.SZ': pd.Series([1e9], index=['20190101'])})
    market = QmtMarket(C, ('A',))
    assert market.bars('SZ000001').close.tolist() == [9.5, 9.5]
    assert set(market.index_closes()) == set(INDEX_SYMBOLS)
    assert market.universe() == ['SH600000', 'SZ000001']
    assert market.name('SZ000001') == 'PING AN' and market.name('SH600000') is None
    assert market.total_shares('SZ000001') == {'2019-01-01': 1e9}


def test_gui_beats_config_beats_default(monkeypatch):
    monkeypatch.setattr(entry, 'CONFIG', {'strategies': '1,2', 'sectors': 'A'})
    context, lines = entry.build_context(FakeContextInfo({}), entry.gui_values({'strategies': 3, 'x': 1}))
    assert sorted(context.strategies) == [3] and context.settings.sectors == ('A',)
    assert lines == ["strategies = (3,)  (gui)", "sectors = ('A',)  (CONFIG)"]
    monkeypatch.setattr(entry, 'CONFIG', {'sector': 'A'})
    with pytest.raises(ConfigError, match="CONFIG: unknown keys"):
        entry.build_context(FakeContextInfo({}), {})


def test_handlebar_reports_signals_and_skips(monkeypatch):
    monkeypatch.setattr(entry, 'CONFIG', {'sectors': 'A'})
    bars = dict(INDEX_BARS, **{'000001.SZ': daily([10.0] * 300), '000002.SZ': daily([10.0] * 10)})
    C = FakeContextInfo(bars, names={'000001.SZ': 'X'}, sectors={'A': ['000001.SZ', '000002.SZ', '000003.SZ']})
    with redirect_stdout(io.StringIO()) as out:
        entry.init(C)
        entry.handlebar(C)
    log = out.getvalue().splitlines()
    assert log[0].startswith('chao: config strategies = (1, 2, 3, 4, 5, 6, 7)  (default)')
    # Flat closes meet CLOSE<=MA(CLOSE,20): raw sell conditions, not orders.
    assert 'chao: signal 2021-02-23 strategy=2 SZ000001 sell' in log
    # Missing bars or share capital are reported, never silently dropped.
    assert any('skipped strategy=1 SZ000003: MissingInput: no daily bars' in line for line in log)
    assert any('skipped strategy=1 SZ000001: MissingInput: FINANCE(1)' in line for line in log)
    assert log[-1] == 'chao: 3 signals, 9 skipped'
