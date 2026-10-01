"""Regression checks against the local TDX data; skipped when it is absent."""
from pathlib import Path
import pandas as pd
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


QFQ_ROOT = Path('/home/fikgol/data/tdx/qfq/asof-2026-09-30')
# Reference stocks across boards, with bonus shares and rights issues.
SAMPLE = ['000006', '000612', '002865', '300451', '300125', '600249', '600977',
          '688593', '688788', '601199', '000560', '920580', '920964', '920278']


def tdx_as_qmt(symbols, gbbq):
    """A FakeContextInfo serving TDX raw bars and GBBQ in QMT's per-share shapes."""
    from chao.market import INDEX_SYMBOLS
    from chao.qmt_source import QMT_INDEX, to_qmt
    from tests.qmt_fake import FakeContextInfo
    bars, divid, shares = {}, {}, {}
    sources = [(to_qmt(s), s) for s in symbols] + [(QMT_INDEX[c], INDEX_SYMBOLS[c]) for c in INDEX_SYMBOLS]
    for qmt_code, symbol in sources:
        raw = read_day(symbol_path(RAW, symbol)).loc[:'2026-09-30']
        raw.index = raw.index.strftime('%Y%m%d')
        bars[qmt_code] = raw
    for symbol in symbols:
        records = gbbq.get(symbol[2:], [])
        divid[to_qmt(symbol)] = {r['Date'][:10].replace('-', ''): [r['C1'] / 10, r['C3'] / 10, 0.0, r['C4'] / 10, r['C2'], 0.0, 1.0]
                                 for r in records if r['Category'] == 1}
        shares[to_qmt(symbol)] = pd.Series({r['Date'][:10].replace('-', ''): r['C4'] * 10000.0
                                            for r in records if r['Category'] == 5})
    names = {to_qmt(s): 'STOCK' + s for s in symbols}
    return FakeContextInfo(bars, divid=divid, shares=shares, names=names)


def test_qmt_adapter_reproduces_the_tdx_path():
    from chao.catalog import strategy_files
    from chao.formulas import load_strategies
    from chao.market import equity_symbol
    from chao.qmt_source import HISTORY_BARS, QmtMarket
    from chao.signals import stock_signals, strategy_universe
    from chao.tdx_source import TdxMarket
    gbbq = load_gbbq(GBBQ)
    symbols = [equity_symbol(c) for c in SAMPLE]
    qmt = QmtMarket(tdx_as_qmt(symbols, gbbq), (), HISTORY_BARS)
    tdx = TdxMarket(RAW, QFQ_ROOT, gbbq, {s[2:]: 'STOCK' + s for s in symbols})
    strategies = load_strategies(strategy_files())
    for symbol in symbols:
        a, b = qmt.bars(symbol), tdx.bars(symbol).tail(HISTORY_BARS)
        pd.testing.assert_frame_equal(a[['open', 'high', 'low', 'close']], b[['open', 'high', 'low', 'close']], check_names=False)
        for sid in (1, 5, 7):
            if not strategy_universe(sid, [symbol]):
                continue
            _, sig_q = stock_signals(strategies[sid], qmt, symbol)
            _, sig_t = stock_signals(strategies[sid], tdx, symbol)
            # MA(250) plus REF warm-up: compare the last 100 bars.
            pd.testing.assert_frame_equal(sig_q.tail(100), sig_t.tail(100), check_names=False)
