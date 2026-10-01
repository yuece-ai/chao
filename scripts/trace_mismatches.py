"""Explain remaining event mismatches using original formula leaf values.

Reference rows are used solely for choosing dates to inspect and scoring;
no reference value changes a bar, signal or execution result.
"""
import argparse, ast, json
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from chao.catalog import strategy_files
from chao.formulas import load_strategies, formula_environment, evaluate, parse
from chao.signals import bind_panel
from chao.reference import read_references
from chao.gbbq import load_gbbq
from chao.market import board_index, equity_symbol, share_series
from chao.tdx_source import TdxMarket

G={}
def initialize(cfg, names):
    G['cfg']=cfg; G['strategies']=load_strategies(strategy_files())
    G['gbbq']=load_gbbq(cfg['gbbq_path']); G['names']=names

def value(v,pos):
    """Value at bar position pos: one-stock panels are (bars, 1), NAMELIKE is
    one value per stock, bars are Series."""
    if isinstance(v,(pd.Series,pd.DataFrame)): v=v.values
    if isinstance(v,np.ndarray):
        v=v[pos,0] if v.ndim==2 else (v[0] if len(v)==1 else v[pos])
    if pd.isna(v): return None
    if hasattr(v,'item'):return v.item()
    return v

def inspect(job):
    sid,code,rows=job;cfg=G['cfg']; sym=equity_symbol(code)
    root=cfg.get('qfq_root_by_strategy',{}).get(str(sid)) or cfg['qfq_root']
    market=TdxMarket(cfg['raw_root'],root,G['gbbq'],G['names'])
    idxcode=board_index(sym); f=market.bars(sym)
    st=G['strategies'][sid];env=formula_environment(st,bind_panel(market,[sym],[f]).bound)
    report=[]
    for r in rows:
        d=pd.Timestamp(r['date']);side='买入条件' if r['direction']=='买开' else '卖出条件'; point={'strategy':sid,'code':code,'date':r['date'],'direction':r['direction'],'kind':r['kind'],'indexc':idxcode}
        if d not in f.index:point['error']='date missing';report.append(point);continue
        pos=f.index.get_loc(d)
        point['formula_condition']=value(env[side],pos);point['bar']={k:value(f[k],pos) for k in ['close','high','low','amount']};point['failed_assignments']=[];point['comparisons']=[]
        for key,expr in st.assignments:
            if key=='A' or key=='买入条件':continue
            if side=='卖出条件' and key!='卖出条件':continue
            if side=='买入条件' and key=='卖出条件':continue
            if not bool(value(env[key],pos)): point['failed_assignments'].append(key)
            for node in ast.walk(parse(expr)):
                if not isinstance(node,ast.Compare): continue
                operands=[value(evaluate(x,env),pos) for x in [node.left,*node.comparators]]
                if 'NAMELIKE' in ast.unparse(node) or any(v is None or isinstance(v,bool) for v in operands): continue
                a,b=operands[0],operands[-1]
                margin=abs(a-b)/max(abs(b),1e-12)
                point['comparisons'].append({'assignment':key,'expression':ast.unparse(node),
                    'values':operands,'holds':bool(value(evaluate(node,env),pos)),'margin':margin})
        point['comparisons'].sort(key=lambda c:c['margin'])
        point['comparisons']=point['comparisons'][:4]
        report.append(point)
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',default='config.json');p.add_argument('--reports',nargs='+',required=True);p.add_argument('--out',default='reports/mismatch-trace.json');p.add_argument('--workers',type=int,default=32);a=p.parse_args();cfg=json.loads(Path(a.config).read_text());ref=read_references('origin');names={r['code']:r['name'] for rows in ref.values() for r in rows};jobs=defaultdict(list)
    # A selected-strategy report must not overwrite successful strategies with
    # the zero counts of strategies that were never run.
    collected={}
    for rp in a.reports:
        x=json.loads(Path(rp).read_text())
        for sid,s in x['strategies'].items():
            if s['events']>0: collected[int(sid)]=s
    for sid,s in collected.items():
        for kind in ['missing','extra']:
            for code,date,direction in s['reference_score'][kind]:jobs[sid,code].append({'date':date,'direction':direction,'kind':kind,'name':names.get(code,'')})
    out=[]
    with ProcessPoolExecutor(max_workers=a.workers,initializer=initialize,initargs=(cfg,names)) as pool:
        for rows in pool.map(inspect,[(sid,code,rows) for (sid,code),rows in jobs.items()]):out.extend(rows)
    Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({'events':len(out),'failed_conditions':sum(r.get('formula_condition') is False for r in out),'failed_assignments':Counter(k for r in out for k in r.get('failed_assignments',[]))},ensure_ascii=False))
if __name__=='__main__':main()
