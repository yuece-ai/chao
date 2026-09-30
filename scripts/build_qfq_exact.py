"""Diagnostic exact affine qfq using decimal corporate-action fields.

GBBQ stores single-precision fields. Their shortest decimal representations
recover nominal action values without retaining binary serialization noise.
Rational arithmetic keeps half-fen boundaries exact throughout composition.
Reference prices are never read by this transform.
"""
from fractions import Fraction
from decimal import Decimal, ROUND_HALF_UP
import numpy as np
import pandas as pd


def field(value):
    value = Decimal(str(np.float32(value))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return Fraction(str(value))


def qfq_exact(raw, records):
    if raw.empty:
        return raw.copy()
    events=[]
    for row in records:
        if int(row.get('Category',0)) != 1:
            continue
        date=pd.Timestamp(row['Date']).tz_localize(None)
        if date > raw.index[-1]:
            continue
        m=(10+field(row['C3'])+field(row['C4']))/10
        c=(field(row['C1'])-field(row['C4'])*field(row['C2']))/10
        if m>0:
            events.append((date,m,c))
    events.sort(reverse=True)
    out=raw.copy()
    a,b=Fraction(1),Fraction(0)
    right=len(raw)
    for date,m,c in events:
        left=int(raw.index.searchsorted(date,side='left'))
        if left<right:
            for key in ('open','high','low','close'):
                values=raw[key].iloc[left:right].to_numpy(dtype=float)
                out.iloc[left:right,out.columns.get_loc(key)]=values*float(a)+float(b)
            right=left
        a/=m
        b-=a*c
    if right:
        for key in ('open','high','low','close'):
            values=raw[key].iloc[:right].to_numpy(dtype=float)
            out.iloc[:right,out.columns.get_loc(key)]=values*float(a)+float(b)
    return out
