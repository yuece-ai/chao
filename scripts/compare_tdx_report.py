#!/usr/bin/env python3
"""Compare the trade lists a QMT backtest wrote (report_path) with the TDX exports.

Prints, per strategy, the reference rows matched on (code, date, direction)
and how many of those also match every accounting field.
"""
import argparse, csv, json
from pathlib import Path
from chao.reference import read_references, score


def read_report(path):
    with open(path, encoding='utf-8') as f:
        return [{'code': r['品种代码'], 'date': r['时间'][:10], 'direction': r['信号'],
                 'price': float(r['价格(元)']), 'quantity': int(r['交易量(股/手)']),
                 'amount': float(r['成交额(元)']), 'fee': float(r['手续费(元)']),
                 'profit': float(r['收益(元)']), 'cash': float(r['可用资金(元)'])}
                for r in csv.DictReader(f, delimiter='\t')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', required=True, help='directory with strategy-<id>-signals.tsv')
    ap.add_argument('--reference-root', default='origin')
    args = ap.parse_args()
    refs = read_references(args.reference_root)
    result = {}
    for path in sorted(Path(args.report).glob('strategy-*-signals.tsv')):
        sid = int(path.stem.split('-')[1])
        s = score(refs.get(sid, []), read_report(path))
        result[sid] = {'expected': s['expected'], 'matched': s['matched'], 'missing': len(s['missing']),
                       'extra': len(s['extra']), 'accounting_matched': s['accounting_matched']}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
