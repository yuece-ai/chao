import pandas as pd
import pytest
from chao.data import board_index, equity_symbol
from chao.qfq import ex_rights, field, forward_adjust


def bars(closes):
    dates = pd.date_range('2020-01-01', periods=len(closes))
    return pd.DataFrame({k: closes for k in ('open', 'high', 'low', 'close')}, index=dates)


def gbbq(date, c1=0, c2=0, c3=0, c4=0, category=1):
    return {'Category': category, 'Code': '000001', 'Date': f'{date}T00:00:00Z',
            'C1': c1, 'C2': c2, 'C3': c3, 'C4': c4}


def test_cash_dividend_shifts_earlier_bars():
    adjusted = forward_adjust(bars([10.0, 9.5]), ex_rights([gbbq('2020-01-02', c1=5)], '2020-01-02'))
    assert adjusted.close.tolist() == [9.5, 9.5]


def test_bonus_rounds_half_away_from_zero_once():
    # 10.01 / 2 = 5.005 exactly, which TDX shows as 5.01.
    adjusted = forward_adjust(bars([10.01, 5.0]), ex_rights([gbbq('2020-01-02', c3=10)], '2020-01-02'))
    assert adjusted.close.tolist() == [5.01, 5.0]


def test_events_after_snapshot_and_other_categories_are_ignored():
    records = [gbbq('2020-01-02', c1=5), gbbq('2020-01-02', c1=9, category=5)]
    assert ex_rights(records, '2020-01-01') == []
    assert len(ex_rights(records, '2020-01-02')) == 1


def test_gbbq_float32_fields_are_read_at_three_decimals():
    assert field(1.7999969720840454) == field(1.8)
    assert float(field(2.522322)) == 2.522


@pytest.mark.parametrize('code,symbol,index', [
    ('600000', 'SH600000', '999999'), ('688001', 'SH688001', '000688'),
    ('000688', 'SZ000688', '399001'), ('300001', 'SZ300001', '399006'),
    ('302132', 'SZ302132', '399006'), ('430047', 'BJ430047', '899050'),
    ('830799', 'BJ830799', '899050'), ('920580', 'BJ920580', '899050')])
def test_equity_symbol_and_board_index(code, symbol, index):
    assert equity_symbol(code) == symbol and board_index(symbol) == index
