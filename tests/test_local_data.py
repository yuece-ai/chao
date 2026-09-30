"""Regression checks against the local TDX data; skipped when it is absent."""
from pathlib import Path
import pytest
from chao.data import read_day, symbol_path
from chao.gbbq import load_gbbq
from chao.qfq import ex_rights, forward_adjust

RAW = Path('/home/fikgol/data/tdx/cryptd-workspace/data/tdx/raw/vipdoc')
GBBQ = Path('/home/fikgol/data/tdx/gbbq.json')
pytestmark = pytest.mark.skipif(not (RAW.exists() and GBBQ.exists()), reason='local TDX data absent')


def test_forward_adjust_matches_reference_trade_prices():
    # Strategy 1 export: 000006 bought 2015-03-20 at 6.06, sold 2015-05-05 at 9.41.
    raw = read_day(symbol_path(RAW, 'SZ000006')).loc[:'2026-09-30']
    adjusted = forward_adjust(raw, ex_rights(load_gbbq(GBBQ)['000006'], '2026-09-30'))
    assert adjusted.close.loc['2015-03-20'] == 6.06
    assert adjusted.close.loc['2015-05-05'] == 9.41
