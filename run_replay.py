#!/usr/bin/env python3
"""Replay the seven TDX formulas on local TDX bars and score them against
the TDX exports in origin/.  This runner never places orders."""
import argparse, json, os, sys
from collections import namedtuple
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from chao.data import equity_files
from chao.market import MissingInput, equity_symbol
from chao.signals import stock_signals
from chao.tdx_source import TdxMarket
from chao.catalog import strategy_files
from chao.formulas import load_strategies
from chao.gbbq import load_gbbq
from chao.replay import ReplaySpec, replay
from chao.reference import read_references, score
from chao.settings import REQUIRED, ConfigError, Field, describe, id_list, integer, load, number, path_map, text

G = {}

FIELDS = [
    Field('raw_root', text, REQUIRED, 'TDX vipdoc directory with unadjusted daily bars'),
    Field('qfq_root', text, REQUIRED, 'forward-adjusted bars built by scripts/build_qfq.py'),
    Field('qfq_root_by_strategy', path_map, {}, 'per-strategy snapshot roots, e.g. 2=/path'),
    Field('gbbq_path', text, REQUIRED, 'TDX GBBQ records as JSON'),
    Field('reference_root', text, REQUIRED, 'directory with the TDX signal exports'),
    Field('report_dir', text, REQUIRED, 'output directory for events and the report'),
    Field('start', text, REQUIRED, 'first replay date, YYYY-MM-DD'),
    Field('end', text, REQUIRED, 'last replay date, YYYY-MM-DD'),
    Field('initial_cash', number, REQUIRED, 'cash per stock'),
    Field('buy_fee_rate', number, REQUIRED, 'fee rate on buy amount'),
    Field('sell_fee_rate', number, REQUIRED, 'fee rate on sell amount'),
    Field('strategies', id_list, (1, 2, 3, 4, 5, 6, 7), 'strategy ids to replay'),
    Field('workers', integer, 1, 'worker processes'),
]
ReplaySettings = namedtuple('ReplaySettings', [f.key for f in FIELDS])


def market_for(sid):
    """Each TDX export carries its own adjustment snapshot (see PARITY.md)."""
    settings = G['settings']
    root = settings.qfq_root_by_strategy.get(sid, settings.qfq_root)
    if root not in G['markets']:
        G['markets'][root] = TdxMarket(settings.raw_root, root, G['gbbq'], G['names'])
    return G['markets'][root]


def replay_spec(settings):
    return ReplaySpec(start=settings.start, end=settings.end, initial_cash=settings.initial_cash,
                      buy_fee_rate=settings.buy_fee_rate, sell_fee_rate=settings.sell_fee_rate)


def init_worker(settings, names):
    G.update(settings=settings, names=names, strategies=load_strategies(strategy_files()),
             spec=replay_spec(settings), markets={})
    G['gbbq'] = load_gbbq(settings.gbbq_path)


def task(item):
    sid, symbol = item
    code = symbol[2:]
    try:
        frame, sig = stock_signals(G['strategies'][sid], market_for(sid), symbol)
        if sig is None:
            return sid, code, [], {'error': 'insufficient history'}
        events, summary = replay(frame, sig, G['spec'])
        for e in events:
            e.update(strategy=sid, code=code)
        return sid, code, events, summary
    except (MissingInput, ValueError, KeyError) as exc:
        return sid, code, [], {'error': f'{type(exc).__name__}: {exc}'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.json')
    ap.add_argument('--set', action='append', default=[], metavar='KEY=VALUE',
                    help='override a setting; takes precedence over the config file')
    ap.add_argument('--show-config', action='store_true', help='print settings with origins and exit')
    ap.add_argument('--reference-only', action='store_true')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()
    flags = dict(item.split('=', 1) for item in args.set)
    try:
        loaded = load(FIELDS, [('--set', flags), (args.config, json.loads(Path(args.config).read_text()))])
    except ConfigError as exc:
        sys.exit('run_replay: {}'.format(exc))
    if args.show_config:
        print('\n'.join(describe(loaded)))
        return
    settings = ReplaySettings(**loaded.values)
    refs = read_references(settings.reference_root)
    names = {r['code']: r['name'] for rows in refs.values() for r in rows}
    if args.reference_only:
        symbols = sorted({equity_symbol(r['code']) for rows in refs.values() for r in rows})
    else:
        symbols = sorted(symbol for symbol, _ in equity_files(settings.raw_root))
    if args.limit:
        symbols = symbols[:args.limit]
    selected = sorted(settings.strategies)
    # Strategy 7 is the Beijing exchange strategy; 1-6 cover SH and SZ.
    jobs = [(sid, s) for sid in selected for s in symbols if (sid == 7) == s.startswith('BJ')]
    workers = max(1, min(settings.workers, os.cpu_count() or 1))
    actual = {sid: [] for sid in range(1, 8)}; errors = []
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=(settings, names)) as pool:
        for sid, code, events, summary in pool.map(task, jobs, chunksize=max(1, len(jobs) // (workers * 8))):
            if 'error' in summary:
                errors.append({'strategy': sid, 'code': code, **summary})
            actual[sid].extend(events)
    out = Path(settings.report_dir); out.mkdir(parents=True, exist_ok=True)
    report = {'settings': describe(loaded), 'workers': workers, 'jobs': len(jobs), 'errors': errors, 'strategies': {}}
    for sid in range(1, 8):
        actual[sid].sort(key=lambda x: (x['date'], x['code'], x['direction']))
        report['strategies'][str(sid)] = {'events': len(actual[sid]),
                                          'reference_score': score(refs.get(sid, []), actual[sid]),
                                          'errors': sum(e['strategy'] == sid for e in errors)}
        (out / f'strategy-{sid}-events.json').write_text(json.dumps(actual[sid], ensure_ascii=False, indent=2))
    (out / 'replay-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    summary = {sid: {'events': r['events'], 'errors': r['errors'],
                     **{k: r['reference_score'][k] for k in ('expected', 'matched', 'accounting_matched')}}
               for sid, r in report['strategies'].items()}
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
