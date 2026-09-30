"""Reference exports are used for scoring, never as generated signals."""
import csv
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# Money fields shown by the TDX export, all rounded to the cent.
MONEY_FIELDS = ('price', 'amount', 'fee', 'profit', 'cash')


def read_references(root):
    result={}
    for path in Path(root).glob('*交易信号.txt'):
        sid=int(path.name[2]); rows=[]
        with path.open(encoding='utf-8') as f:
            for row in csv.DictReader(f,delimiter='\t'):
                if row['品种代码'].isdigit():
                    rows.append({'code':row['品种代码'],'name':row['品种名称'],
                                 'date':row['时间'][:10],'direction':row['信号'],
                                 'price':float(row['价格(元)']),'quantity':int(float(row['交易量(股/手)'])),
                                 'amount':float(row['成交额(元)']),'fee':float(row['手续费(元)']),
                                 'profit':float(row['收益(元)']),'cash':float(row['可用资金(元)'])})
        result[sid]=rows
    return result


def cents(value):
    """Round a stored value the way the export displays it."""
    return float(Decimal(repr(float(value))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def field_mismatches(expected, actual):
    wrong = [] if expected['quantity'] == actual['quantity'] else ['quantity']
    wrong += [k for k in MONEY_FIELDS if abs(cents(actual[k]) - expected[k]) > 0.001]
    return [{'field': k, 'expected': expected[k], 'actual': actual[k]} for k in wrong]


def score(expected, actual):
    key=lambda r:(r['code'],r['date'],r['direction'])
    expected_map={key(r):r for r in expected}; actual_map={key(r):r for r in actual}
    expected_counts=Counter(map(key,expected)); actual_counts=Counter(map(key,actual))
    common=sorted(expected_counts.keys() & actual_counts.keys())
    missing=sorted(list(k) for k,n in (expected_counts-actual_counts).items() for _ in range(n))
    extra=sorted(list(k) for k,n in (actual_counts-expected_counts).items() for _ in range(n))
    accounting=[]
    for k in common:
        wrong=field_mismatches(expected_map[k], actual_map[k])
        if wrong: accounting.append({'code':k[0],'date':k[1],'direction':k[2],'fields':wrong})
    return {'expected':len(expected),'actual':len(actual),
            'matched':sum(min(expected_counts[k],actual_counts[k]) for k in common),
            'accounting_matched':len(common)-len(accounting),
            'missing':missing,'extra':extra,'accounting_mismatches':accounting}
