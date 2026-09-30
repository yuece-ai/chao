"""Reference exports are used for scoring, never as generated signals."""
import csv
from collections import Counter
from pathlib import Path


def read_references(root):
    result={}
    for path in Path(root).glob('*交易信号.txt'):
        sid=int(path.name[2]); rows=[]
        with path.open(encoding='utf-8') as f:
            for row in csv.DictReader(f,delimiter='\t'):
                if row['品种代码'].isdigit():
                    rows.append({'code':row['品种代码'],'name':row['品种名称'],
                                 'date':row['时间'][:10],'direction':row['信号'],
                                 'price':float(row['价格(元)']),'quantity':float(row['交易量(股/手)']),
                                 'amount':float(row['成交额(元)']),'fee':float(row['手续费(元)']),
                                 'profit':float(row['收益(元)'])})
        result[sid]=rows
    return result


def score(expected, actual):
    expected_rows=list(expected); actual_rows=list(actual)
    expected_map={(r['code'],r['date'],r['direction']):r for r in expected_rows}
    actual_map={(r['code'],r['date'],r['direction']):r for r in actual_rows}
    expected_counts=Counter((r['code'],r['date'],r['direction']) for r in expected_rows)
    actual_counts=Counter((r['code'],r['date'],r['direction']) for r in actual_rows)
    common=sorted(expected_counts.keys() & actual_counts.keys())
    matched=sum(min(expected_counts[k], actual_counts[k]) for k in common)
    missing=[]
    for key,count in (expected_counts-actual_counts).items(): missing.extend([list(key)]*count)
    extra=[]
    for key,count in (actual_counts-expected_counts).items(): extra.extend([list(key)]*count)
    return {'expected':len(expected_rows),'actual':len(actual_rows),'matched':matched,
            'missing':sorted(missing),'extra':sorted(extra),
            'price_errors':[{'code':k[0],'date':k[1],'direction':k[2],
                             'error':actual_map[k]['price']-expected_map[k]['price']} for k in common]}
