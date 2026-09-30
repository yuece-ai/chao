#!/usr/bin/env python3
"""Build one forward-adjusted (qfq) root as of a snapshot date.

Writes <out>/<SYMBOL>.csv for every A-share stock in the raw TDX tree and
for the formula indices (indices have no ex-rights events and are copied).
"""
import argparse, json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from chao.data import INDEX_SYMBOLS, equity_files, read_day, symbol_path
from chao.gbbq import load_gbbq
from chao.qfq import PRICE_COLUMNS, ex_rights, forward_adjust

G = {}


def init(raw_root, out, as_of, gbbq_path):
    G.update(raw=raw_root, out=Path(out), as_of=as_of, records=load_gbbq(gbbq_path))


def build(item):
    symbol, is_index = item
    raw = read_day(symbol_path(G['raw'], symbol)).loc[:G['as_of']]
    if raw.empty:
        return symbol, 'empty'
    events = [] if is_index else ex_rights(G['records'].get(symbol[2:], []), G['as_of'])
    adjusted = forward_adjust(raw, events)
    adjusted[list(PRICE_COLUMNS)].to_csv(G['out'] / f'{symbol}.csv', index_label='date', float_format='%.2f')
    return symbol, 'ok'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw-root', required=True)
    ap.add_argument('--gbbq', required=True)
    ap.add_argument('--as-of', required=True, help='snapshot date, YYYY-MM-DD')
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=48)
    args = ap.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    items = [(s, False) for s, _ in equity_files(args.raw_root)] + [(s, True) for s in INDEX_SYMBOLS.values()]
    with ProcessPoolExecutor(args.workers, initializer=init,
                             initargs=(args.raw_root, args.out, args.as_of, args.gbbq)) as pool:
        results = list(pool.map(build, items, chunksize=16))
    status = defaultdict(int)
    for _, s in results:
        status[s] += 1
    print(json.dumps({'as_of': args.as_of, 'out': args.out, **status}))


if __name__ == '__main__':
    main()
