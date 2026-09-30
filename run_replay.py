#!/usr/bin/env python3
"""Replay the seven TDX formulas on local TDX bars and score them against
the TDX exports in origin/.  This runner never places orders."""
import argparse, json, os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pandas as pd
from chao.data import (INDEX_SYMBOLS, MissingInput, board_index, equity_files,
                       equity_symbol, load_prices)
from chao.formulas import strategies, signals
from chao.replay import ReplaySpec, replay
from chao.reference import read_references, score

SOURCE = 'origin/策略源码.txt'
MIN_BARS = 260
G = {}


def strategy_config(config, sid):
    """Each TDX export carries its own adjustment snapshot (see PARITY.md)."""
    root = config.get('qfq_root_by_strategy', {}).get(str(sid))
    return {**config, 'qfq_root': root} if root else config


def replay_spec(config):
    return ReplaySpec(start=config['start'], end=config['end'],
                      initial_cash=float(config['initial_cash']),
                      buy_fee_rate=float(config['buy_fee_rate']),
                      sell_fee_rate=float(config['sell_fee_rate']))


def indices_for(config):
    root = config['qfq_root']
    if root not in G['indices']:
        G['indices'][root] = {code: load_prices(config, symbol)['close']
                              for code, symbol in INDEX_SYMBOLS.items()}
    return G['indices'][root]


def share_capital(code, index):
    """FINANCE(1): total shares, stored in units of 10,000 shares."""
    history = G['finance'].get(code)
    if not history:
        return None
    series = pd.Series({pd.Timestamp(d): float(v) for d, v in history.items()}).sort_index()
    return series.reindex(index, method='ffill').fillna(0.0) * 10000.0


def init_worker(config, names):
    G.update(config=config, names=names, strategies=strategies(SOURCE),
             spec=replay_spec(config), indices={})
    G['finance'] = json.loads(Path(config['finance_path']).read_text())


def task(item):
    sid, symbol = item
    code = symbol[2:]
    try:
        config = strategy_config(G['config'], sid)
        frame = load_prices(config, symbol)
        if len(frame) < MIN_BARS:
            return sid, code, [], {'error': 'insufficient history'}
        indices = indices_for(config)
        sig = signals(G['strategies'][sid], frame, indices, indices[board_index(symbol)],
                      G['names'].get(code, ''), share_capital(code, frame.index))
        events, summary = replay(frame, sig, G['spec'])
        for e in events:
            e.update(strategy=sid, code=code)
        return sid, code, events, summary
    except (MissingInput, ValueError, KeyError) as exc:
        return sid, code, [], {'error': f'{type(exc).__name__}: {exc}'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.json')
    ap.add_argument('--reference-only', action='store_true')
    ap.add_argument('--workers', type=int)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--strategies', default='1,2,3,4,5,6,7')
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    refs = read_references(cfg['reference_root'])
    names = {r['code']: r['name'] for rows in refs.values() for r in rows}
    if args.reference_only:
        symbols = sorted({equity_symbol(r['code']) for rows in refs.values() for r in rows})
    else:
        symbols = sorted(symbol for symbol, _ in equity_files(cfg['raw_root']))
    if args.limit:
        symbols = symbols[:args.limit]
    selected = sorted(int(x) for x in args.strategies.split(',') if x)
    # Strategy 7 is the Beijing exchange strategy; 1-6 cover SH and SZ.
    jobs = [(sid, s) for sid in selected for s in symbols if (sid == 7) == s.startswith('BJ')]
    workers = max(1, min(args.workers or int(cfg.get('workers') or 1), os.cpu_count() or 1))
    actual = {sid: [] for sid in range(1, 8)}; errors = []
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=(cfg, names)) as pool:
        for sid, code, events, summary in pool.map(task, jobs, chunksize=max(1, len(jobs) // (workers * 8))):
            if 'error' in summary:
                errors.append({'strategy': sid, 'code': code, **summary})
            actual[sid].extend(events)
    out = Path(cfg['report_dir']); out.mkdir(parents=True, exist_ok=True)
    report = {'config': cfg, 'workers': workers, 'jobs': len(jobs), 'errors': errors, 'strategies': {}}
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
