"""TDX forward adjustment (qfq) from GBBQ ex-rights records.

TDX adjusts every bar before an ex-date with the affine transform
    P' = (P - C1/10 + C2*C4/10) / (1 + (C3 + C4)/10)
where, for GBBQ category 1, C1 is cash per 10 shares, C2 the rights-issue
price, C3 bonus shares per 10 and C4 rights shares per 10.  The composed
transform is evaluated exactly and rounded once, half away from zero, to the
fen.  GBBQ stores the fields as float32; TDX reads them at three decimals
(e.g. 1.799997 -> 1.800, 0.600215 -> 0.600).

An export only contains the ex-dates known when it was produced, so the
transform takes an explicit as-of date.  These rules reproduce 15,115 of the
15,118 reference trade prices; the three exceptions sit within 5e-5 of a
half-fen boundary.
"""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import NamedTuple
import numpy as np
import pandas as pd

PRICE_COLUMNS = ('open', 'high', 'low', 'close')
XRXD = 1


class ExRights(NamedTuple):
    date: pd.Timestamp
    cash: Fraction          # C1, yuan per 10 shares
    rights_price: Fraction  # C2, yuan per share
    bonus: Fraction         # C3, shares per 10
    rights: Fraction        # C4, shares per 10


def field(value):
    text = str(np.float32(value))
    return Fraction(str(Decimal(text).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)))


def ex_rights(records, as_of):
    """Category-1 GBBQ records with ex-date on or before as_of, oldest first."""
    as_of = pd.Timestamp(as_of)
    events = []
    for row in records:
        if int(row['Category']) != XRXD:
            continue
        date = pd.Timestamp(row['Date']).tz_localize(None)
        if date <= as_of:
            events.append(ExRights(date, field(row['C1']), field(row['C2']),
                                   field(row['C3']), field(row['C4'])))
    return sorted(events, key=lambda e: e.date)


def _round_cents(cents, a, b):
    """Round (cents/100)*a + b to cents, half away from zero, exactly."""
    num = [c * a.numerator * b.denominator + 100 * b.numerator * a.denominator for c in cents.tolist()]
    den = a.denominator * b.denominator
    return np.array([(1 if n >= 0 else -1) * ((2 * abs(n) + den) // (2 * den)) for n in num],
                    dtype=np.int64)


def forward_adjust(raw, events):
    """Return raw OHLC bars forward-adjusted by events (see module doc)."""
    out = raw[list(PRICE_COLUMNS)].copy()
    cents = {k: np.rint(raw[k].values * 100).astype(np.int64) for k in PRICE_COLUMNS}
    a, b = Fraction(1), Fraction(0)
    right = len(raw)
    for event in reversed(events):
        left = int(raw.index.searchsorted(event.date, side='left'))
        if left < right and (a, b) != (1, 0):
            for k in PRICE_COLUMNS:
                out.iloc[left:right, out.columns.get_loc(k)] = _round_cents(cents[k][left:right], a, b) / 100
        right = min(right, left)
        scale = 1 + (event.bonus + event.rights) / 10
        shift = event.cash / 10 - event.rights_price * event.rights / 10
        # Compose P -> (P - shift) / scale after the existing transform.
        a, b = a / scale, b - a * shift / scale
    if right and (a, b) != (1, 0):
        for k in PRICE_COLUMNS:
            out.iloc[:right, out.columns.get_loc(k)] = _round_cents(cents[k][:right], a, b) / 100
    return out
