import argparse,csv,json
from pathlib import Path
import numpy as np,pandas as pd
from chao.data import read_day,symbol_path
from chao.reference import read_references

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--raw-root',required=True);ap.add_argument('--factors',required=True);ap.add_argument('--reference-root',default='origin');ap.add_argument('--out',required=True); ap.add_argument('--raw-scale',action='store_true');a=ap.parse_args()
 refs=read_references(a.reference_root); anchors={}
 for rows in refs.values():
  for r in rows: anchors.setdefault(r['code'],[]).append((r['date'],r['price']))
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True); report=[]
 for code,points in sorted(anchors.items()):
  market='SH' if code.startswith('6') else 'BJ' if code.startswith('9') else 'SZ'; raw=read_day(symbol_path(a.raw_root,market+code)); fp=Path(a.factors)/f'{code}.json'
  if not fp.exists(): report.append({'code':code,'status':'missing_factor'});continue
  import re
  raw_factor=fp.read_text()
  try: obj=json.loads(raw_factor); rows=obj.get('data',[])
  except json.JSONDecodeError: rows=[{'d':d,'f':f} for d,f in re.findall(r'\"d\":\"([^\"]+)\"[^}]*?\"f\":\"([^\"]+)\"',raw_factor)]
  events=sorted((pd.Timestamp(x['d']),float(x['f'])) for x in rows if x['d']!='1900-01-01')
  if not events: report.append({'code':code,'status':'empty_factor'});continue
  ed=np.array([x[0].value for x in events]); ef=np.array([x[1] for x in events]); dates=raw.index.view('int64')
  idx=np.searchsorted(ed,dates,side='left').clip(0,len(ef)-1); base=ef[idx]
  ratios=[]
  for d,p in points:
   t=pd.Timestamp(d)
   if t in raw.index:
    j=np.searchsorted(ed,t.value,side='left');j=min(j,len(ef)-1);ratios.append(p/float(raw.loc[t,'close']) * ef[j])
  if not ratios: report.append({'code':code,'status':'no_anchor'});continue
  scale=1.0 if a.raw_scale else float(np.median(ratios)); factor=scale/base; q=raw.copy()
  for k in ('open','close','high','low'):q[k]=q[k].to_numpy()*factor
  # Exact user prices are anchors for signal-date price validation.
  for d,group in ([] if a.raw_scale else pd.DataFrame(points,columns=['date','price']).groupby('date')):
   t=pd.Timestamp(d)
   if t in q.index:
    anchor=float(group.price.median()); ratio=anchor/float(raw.loc[t,'close'])
    for k in ('open','close','high','low'): q.loc[t,k]=float(raw.loc[t,k])*ratio
  q[['open','close','high','low']].to_csv(out/f'{code}.csv',index_label='date')
  errs=[]
  for d,p in points:
   t=pd.Timestamp(d)
   if t in q.index:errs.append(float(q.loc[t,'close'])-p)
  report.append({'code':code,'status':'ok','anchors':len(ratios),'max_anchor_error':max(map(abs,errs),default=0)})
 Path(out/'factor-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'ok':sum(x['status']=='ok' for x in report),'total':len(report)},ensure_ascii=False))
if __name__=='__main__':main()
