"""TDX indicator functions over daily bars.

Each indicator takes pandas Series and integer periods and returns a Series
on the same index. Formulas can only call indicators registered in
INDICATORS; add new ones here without touching the formula engine.
Plain arrays (results of formula arithmetic) are accepted as positional series.
"""
import pandas as pd


def series(x):
    return x if isinstance(x, pd.Series) else pd.Series(x)


def MA(x, n):
    """Simple moving average; undefined until n bars exist."""
    return series(x).rolling(int(n), min_periods=int(n)).mean()


def REF(x, n):
    """Value n bars ago."""
    return series(x).shift(int(n))


def HHV(x, n):
    """Highest value over the last n bars."""
    return series(x).rolling(int(n), min_periods=int(n)).max()


def LLV(x, n):
    """Lowest value over the last n bars."""
    return series(x).rolling(int(n), min_periods=int(n)).min()


INDICATORS = {'MA': MA, 'REF': REF, 'HHV': HHV, 'LLV': LLV}
