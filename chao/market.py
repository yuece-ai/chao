"""Market data seen by the strategies, independent of where it comes from.

Symbols are exchange-qualified ('SH600000'). MarketData has two sources:
TDX files on Linux (chao.tdx_source) and the QMT client (chao.qmt_source).
Strategy code only receives a MarketData and plain pandas data from it.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, NamedTuple
import pandas as pd


class MissingInput(ValueError):
    pass


# Index symbols used by the formulas.  000688 is both a Shenzhen stock and
# the Shanghai STAR 50 index, so indices are always spelled out in full.
INDEX_SYMBOLS = {'999999': 'SH999999', '399001': 'SZ399001', '399006': 'SZ399006',
                 '000688': 'SH000688', '899050': 'BJ899050'}


def equity_symbol(code):
    """Exchange-qualified symbol for a six-digit A-share stock code."""
    if code.startswith('6'):
        return 'SH' + code
    if code.startswith(('0', '3')):
        return 'SZ' + code
    if code.startswith(('43', '83', '87', '92')):
        return 'BJ' + code
    raise ValueError('cannot infer exchange for stock code {!r}'.format(code))


def board_index(symbol):
    """Index code TDX binds to INDEXC for a stock's board."""
    if symbol.startswith('BJ'):
        return '899050'
    if symbol.startswith(('SH688', 'SH689')):
        return '000688'
    if symbol.startswith(('SZ300', 'SZ301', 'SZ302')):
        return '399006'
    return '399001' if symbol.startswith('SZ') else '999999'


class MarketData(ABC):
    """Daily data for one snapshot of the market."""

    @abstractmethod
    def universe(self):
        """Exchange-qualified stock symbols."""

    @abstractmethod
    def bars(self, symbol):
        """Forward-adjusted daily bars: DatetimeIndex; open high low close amount."""

    @abstractmethod
    def index_closes(self):
        """{index code: daily close Series} for every code in INDEX_SYMBOLS."""

    @abstractmethod
    def name(self, symbol):
        """Current stock name, or None when unknown."""

    @abstractmethod
    def total_shares(self, symbol):
        """Total-share steps {YYYY-MM-DD: shares}; empty when unknown."""


class Context(NamedTuple):
    """Everything one run needs: settings, market data and the strategies."""
    settings: Any
    market: MarketData
    strategies: Dict[int, Any]


def share_series(steps, index):
    """FINANCE(1) on each bar from share-capital steps, or None."""
    if not steps:
        return None
    series = pd.Series({pd.Timestamp(d): float(v) for d, v in steps.items()}).sort_index()
    return series.reindex(index, method='ffill').fillna(0.0)
