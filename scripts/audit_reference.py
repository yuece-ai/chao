"""Audit reference coverage, displayed prices and implied fee rates."""
import json
from pathlib import Path
import numpy as np, pandas as pd
from chao.reference import read_references
from chao.data import read_day,symbol_path

def main():
 cfg=json.loads(Path('config.json').read_text()); refs=read_references('origin'); cache={}; qcache={}; summary={}
 for sid,rows in refs.items():
  misses=[]; price_errors=[]; fee_rates={'买开':[],'卖平':[]}; actions={}
  for r in rows:
   code=r['code'];symbol=('SH' if code.startswith('6') else 'BJ' if code.startswith('9') else 'SZ')+code
   if code not in cache:cache[code]=read_day(symbol_path(cfg['raw_root'],symbol))
   if r['date'] not in cache[code].index: misses.append([code,r['date']])
   qpath=Path(cfg['qfq_root'])/f'{code}.csv'
   if code not in qcache:qcache[code]=pd.read_csv(qpath).set_index('date') if qpath.exists() else None
   q=qcache[code]
   if q is not None and r['date'] in q.index:price_errors.append(float(q.loc[r['date'],'close'])-r['price'])
   if r['amount'] and r['direction'] in fee_rates: fee_rates[r['direction']].append(r['fee']/r['amount'])
   actions[r['direction']]=actions.get(r['direction'],0)+1
  summary[sid]={'events':len(rows),'actions':actions,'missing_raw_dates':misses,
   'independent_price_comparisons':len(price_errors),'within_0_005':sum(abs(x)<=.0050001 for x in price_errors),
   'max_price_error':max(map(abs,price_errors),default=None),
   'median_implied_fee_rates':{k:float(np.median(v)) if v else None for k,v in fee_rates.items()}}
 out=Path('reports/reference-audit.json');out.write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
