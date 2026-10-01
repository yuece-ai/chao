"""TDX indicator functions over daily bars.

Each indicator takes pandas Series and integer periods and returns a Series
on the same index. Formulas can only call indicators registered in
INDICATORS; add new ones here without touching the formula engine.
Inputs are positional: one stock as a Series, or a panel of stocks as a
DataFrame with one column per stock (rows are each stock's own bars, so
rolling windows never mix stocks). Plain arrays from formula arithmetic are
accepted too.
"""
import pandas as pd


def series(x):
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return x
    return pd.Series(x) if x.ndim == 1 else pd.DataFrame(x)


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
