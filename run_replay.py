#!/usr/bin/env python3
"""Parallel local replay for the seven TDX formulas.

This runner reads the raw TDX daily files produced by cryptd. It never places orders.
"""
import argparse, json, os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pandas as pd
from chao.data import equity_files, symbol_path, load_prices
from chao.formulas import strategies, signals
from chao.replay import replay
from chao.reference import read_references, score

G = {}


def _strategy_qfq_config(config, sid):
    """Return an optional strategy-specific qfq view for historical exports.

    The normal path keeps one current qfq root.  A mapping is useful only when
    reproducing independently exported TDX reports whose adjustment snapshots
    were taken on different dates.
    """
    roots = config.get('qfq_root_by_strategy', {})
    root = roots.get(str(sid), roots.get(sid))
    if not root:
        return config
    return {**config, 'qfq_root': root}


def _indices_for_config(config):
    root = config.get('qfq_root')
    if not root or root == G.get('indices_root'):
        return G['indices']
    cached = G.setdefault('indices_by_root', {}).get(root)
    if cached is not None:
        return cached
    result = {}
    for key, symbol in G['index_symbols'].items():
        result[key] = load_prices(config, symbol)['close']
    G['indices_by_root'][root] = result
    return result


def init_worker(raw_root, source_file, names, index_paths, config):
    global G
    G['strategies']=strategies(source_file); G['names']=names; G['config']=config
    G['index_symbols']={k: ('SH' if k in ('999999','000688') else 'BJ' if k=='899050' else 'SZ')+k for k in index_paths}
    G['indices_root']=config.get('qfq_root')
    G['indices']={k:load_prices(config,G['index_symbols'][k])['close'] for k in index_paths}
    # Historical total share capital (FINANCE(1)), reconstructed from TDX
    # GBBQ category-5 snapshots. Values are stored in 10-thousand shares.
    fp=config.get('finance_path')
    G['finance']={}
    if fp and Path(fp).exists():
        with Path(fp).open() as f: G['finance']=json.load(f)

def task(item):
    sid,symbol,path=item
    try:
        full_symbol=Path(path).stem.upper()
        strategy_config = _strategy_qfq_config(G['config'], sid)
        frame=load_prices(strategy_config,full_symbol)
        if len(frame)<260: return sid,symbol,[],{'error':'insufficient history'}
        shares=G['finance'].get(full_symbol[2:], 0)
        if shares:
            fs=pd.Series({pd.Timestamp(d):float(v) for d,v in shares.items()})
            shares=fs.reindex(frame.index, method='ffill').fillna(0.0)*10000.0
        indices = _indices_for_config(strategy_config)
        indexc = indices['899050'] if full_symbol.startswith('BJ') else indices['000688'] if full_symbol.startswith(('SH688','SH689')) else indices['399006'] if full_symbol.startswith(('SZ300','SZ301','SZ302')) else indices['399001'] if full_symbol.startswith('SZ') else indices['999999']
        sig=signals(G['strategies'][sid],frame,indices,indexc,G['names'].get(symbol,''),shares)
        events,summary=replay(frame,sig,strategy_config)
        for e in events: e.update(strategy=sid,code=symbol)
        return sid,symbol,events,summary
    except Exception as exc:
        return sid,symbol,[],{'error':f'{type(exc).__name__}: {exc}'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--config',default='config.json'); ap.add_argument('--reference-only',action='store_true'); ap.add_argument('--workers',type=int); ap.add_argument('--limit',type=int); ap.add_argument('--strategies',default='1,2,3,4,5,6,7')
    args=ap.parse_args(); cfg=json.loads(Path(args.config).read_text()); raw=Path(cfg['raw_root'])
    refs=read_references(cfg['reference_root']); names={r['code']:r['name'] for rows in refs.values() for r in rows}
    indexes={}
    for code in ('399001','399006','999999','899050','000688'):
        symbol=('SH' if code in ('999999','000688') else 'BJ' if code=='899050' else 'SZ')+code
        indexes[code]=symbol_path(raw,symbol)
    files=dict(equity_files(raw)); symbols=sorted({r['code'] for rows in refs.values() for r in rows}) if args.reference_only else sorted({s[2:] for s in files})
    if args.limit: symbols=symbols[:args.limit]
    selected={int(x) for x in args.strategies.split(',') if x}
    jobs=[(sid,s,files.get(('SH' if s.startswith('6') else 'BJ' if s.startswith('9') else 'SZ')+s, symbol_path(raw,('SH' if s.startswith('6') else 'BJ' if s.startswith('9') else 'SZ')+s))) for sid in sorted(selected) for s in symbols if (sid==7)==s.startswith('9')]
    workers=args.workers or int(cfg.get('workers') or os.cpu_count() or 1)
    workers=max(1,min(workers,os.cpu_count() or 1))
    actual={sid:[] for sid in range(1,8)}; errors=[]
    with ProcessPoolExecutor(max_workers=workers,initializer=init_worker,initargs=(raw,'origin/策略源码.txt',names,indexes,cfg)) as pool:
        for sid,symbol,events,summary in pool.map(task,jobs,chunksize=max(1,len(jobs)//(workers*8))):
            if 'error' in summary: errors.append({'strategy':sid,'code':symbol,**summary})
            actual[sid].extend(events)
    out=Path(cfg.get('report_dir','reports'));  out.mkdir(exist_ok=True)
    report={'config':cfg,'workers':workers,'jobs':len(jobs),'errors':errors,'strategies':{}}
    for sid in range(1,8):
        actual[sid].sort(key=lambda x:(x['date'],x['code'],x['direction']))
        report['strategies'][str(sid)]={'events':len(actual[sid]),'reference_score':score(refs.get(sid,[]),actual[sid]),'errors':sum(e['strategy']==sid for e in errors)}
        Path(out/f'strategy-{sid}-events.json').write_text(json.dumps(actual[sid],ensure_ascii=False,indent=2,default=str))
    Path(out/'replay-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({sid:{k:v for k,v in r.items() if k!='reference_score'} | {'expected':r['reference_score']['expected'],'matched':r['reference_score']['matched']} for sid,r in report['strategies'].items()},indent=2))
if __name__=='__main__': main()
