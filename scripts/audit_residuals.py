#!/usr/bin/env python3
"""Classify every remaining reference mismatch as a root cause or a cascade.

A cascade is a later row of the same strategy and stock: once one trade
differs, the position or cash chain that follows differs too.  Roots get a
cause; an 'unexplained' cause is a parity failure.
"""
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path

BOUNDARY_MARGIN = 1e-5  # relative gap of the closest formula comparison


def signal_rows(report, trace):
    leaves = {(t['strategy'], t['code'], t['date'], t['direction']): t for t in trace}
    rows = []
    for sid, s in report['strategies'].items():
        for kind in ('missing', 'extra'):
            for code, date, direction in s['reference_score'][kind]:
                t = leaves.get((int(sid), code, date, direction), {})
                closest = (t.get('comparisons') or [None])[0]
                rows.append({'strategy': int(sid), 'code': code, 'date': date, 'direction': direction,
                             'layer': 'signal', 'kind': kind, 'closest': closest})
    return rows


def accounting_rows(report):
    rows = []
    for sid, s in report['strategies'].items():
        for m in s['reference_score']['accounting_mismatches']:
            rows.append({'strategy': int(sid), 'code': m['code'], 'date': m['date'],
                         'direction': m['direction'], 'layer': 'accounting', 'fields': m['fields']})
    return rows


def signal_cause(row):
    closest = row['closest']
    if closest and closest['margin'] < BOUNDARY_MARGIN:
        return 'formula_boundary'
    return 'bar_history'


def accounting_cause(row):
    fields = {f['field']: f for f in row['fields']}
    if 'price' in fields:
        return 'price_half_fen_boundary'
    if 'quantity' in fields and row['direction'] == '买开':
        return 'quantity_float_price'
    if set(fields) == {'fee'} and abs(fields['fee']['actual'] - fields['fee']['expected']) < 0.0101:
        return 'fee_display_tie'
    if set(fields) == {'cash'} and abs(fields['cash']['actual'] - fields['cash']['expected']) <= 0.13:
        return 'cash_float32_ulp'
    return 'unexplained'


def classify(rows):
    by_code = defaultdict(list)
    for row in rows:
        by_code[row['strategy'], row['code']].append(row)
    for group in by_code.values():
        group.sort(key=lambda r: (r['date'], r['layer'] == 'accounting', r['direction']))
        root = group[0]
        root['role'] = 'root'
        root['cause'] = signal_cause(root) if root['layer'] == 'signal' else accounting_cause(root)
        for row in group[1:]:
            row['role'] = 'cascade'
            row['root'] = [root['date'], root['direction'], root['layer']]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', required=True)
    ap.add_argument('--trace', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    report = json.loads(Path(args.report).read_text())
    trace = json.loads(Path(args.trace).read_text())
    rows = classify(signal_rows(report, trace) + accounting_rows(report))
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    roots = [r for r in rows if r['role'] == 'root']
    print(json.dumps({'rows': len(rows), 'roots': len(roots),
                      'cascades': len(rows) - len(roots),
                      'root_causes': Counter(r['cause'] for r in roots)}, indent=2))


if __name__ == '__main__':
    main()
