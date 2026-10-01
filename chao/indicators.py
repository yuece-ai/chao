"""TDX indicator functions over daily bars.

Each indicator takes pandas Series and integer periods and returns a Series
on the same index. Formulas can only call indicators registered in
INDICATORS; add new ones here without touching the formula engine.
"""


def MA(x, n):
    """Simple moving average; undefined until n bars exist."""
    return x.rolling(int(n), min_periods=int(n)).mean()


def REF(x, n):
    """Value n bars ago."""
    return x.shift(int(n))


def HHV(x, n):
    """Highest value over the last n bars."""
    return x.rolling(int(n), min_periods=int(n)).max()


def LLV(x, n):
    """Lowest value over the last n bars."""
    return x.rolling(int(n), min_periods=int(n)).min()


INDICATORS = {'MA': MA, 'REF': REF, 'HHV': HHV, 'LLV': LLV}
