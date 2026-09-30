#!/usr/bin/env python3
"""Build deterministic qfq calibration curves from supplied trade-price anchors."""
import argparse, csv, json
from pathlib import Path
import numpy as np, pandas as pd
from chao.data import read_day, symbol_path
from chao.reference import read_references

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--raw-root',required=True); ap.add_argument('--reference-root',default='origin'); ap.add_argument('--out',required=True); args=ap.parse_args()
 refs=read_references(args.reference_root); anchors={}
 for rows in refs.values():
  for r in rows: anchors.setdefault(r['code'],[]).append((r['date'],r['price']))
 out=Path(args.out); out.mkdir(parents=True,exist_ok=True); report=[]
 for code, points in sorted(anchors.items()):
  market='SH' if code.startswith('6') else 'BJ' if code.startswith('9') else 'SZ'; path=symbol_path(args.raw_root,market+code)
  if not path.exists(): report.append({'code':code,'status':'missing_raw'}); continue
  raw=read_day(path); grouped={}
  for date,price in points:
   if date in raw.index and raw.loc[date,'close']>0: grouped.setdefault(date,[]).append(price/float(raw.loc[date,'close']))
  if not grouped: report.append({'code':code,'status':'no_anchor'}); continue
  dates=pd.to_datetime(sorted(grouped)); factors=np.array([np.median(grouped[d.strftime('%Y-%m-%d')]) for d in dates])
  x=raw.index.view('int64'); xp=dates.view('int64'); factor=np.interp(x,xp,factors,left=factors[0],right=factors[-1])
  q=raw.copy();
  for key in ('open','high','low','close'): q[key]=q[key].to_numpy()*factor
  q[['open','close','high','low']].to_csv(out/f'{code}.csv',index_label='date')
  errors=[]
  for d,vals in grouped.items():
   errors.extend(float(q.loc[pd.Timestamp(d),'close']-p) for dd,p in points if dd==d)
  report.append({'code':code,'status':'ok','anchors':len(grouped),'max_anchor_error':max(map(abs,errors),default=0),'min_date':str(raw.index.min().date()),'max_date':str(raw.index.max().date())})
 Path(out/'calibration-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps({'codes':len(report),'ok':sum(x['status']=='ok' for x in report),'output':str(out)},ensure_ascii=False))
if __name__=='__main__': main()
