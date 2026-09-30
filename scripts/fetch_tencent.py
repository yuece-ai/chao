"""Independent adjusted daily bars; cache every provider response."""
import concurrent.futures as cf
import csv,json,time
from pathlib import Path
from urllib.request import urlopen
import argparse

ROOT=Path('/home/fikgol/data/tdx/qfq-tencent')
def fetch_symbol(symbol):
 out=ROOT/f'{symbol[2:]}.csv'
 if out.exists(): return symbol,'cached'
 frames={}
 for year in range(2008,2027,2):
  end=f'{min(year+1,2026)}-12-31' if year<2026 else '2026-09-30'
  cache=ROOT/'responses'/f'{symbol}-{year}.json'; cache.parent.mkdir(parents=True,exist_ok=True)
  for attempt in range(4):
   try:
    if cache.exists(): data=json.loads(cache.read_text())
    else:
     url=f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,{year}-01-01,{end},640,qfq'
     with urlopen(url,timeout=20) as r: data=json.loads(r.read())
     cache.write_text(json.dumps(data,ensure_ascii=False))
    info=data.get('data',{}).get(symbol,{})
    rows=info.get('qfqday',info.get('day',[]))
    if not rows and data.get('code') not in (0,None): raise ValueError(str(data)[:100])
    for row in rows:
     if f'{year}-01-01'<=row[0]<=end: frames[row[0]]=row[:5]
    break
   except Exception as e:
    if attempt==3:return symbol,f'error: {e}'
    time.sleep(attempt+1)
 if not frames:return symbol,'empty'
 with out.open('w') as f:
  writer=csv.writer(f);writer.writerow(['date','open','close','high','low']);writer.writerows(frames[d] for d in sorted(frames))
 return symbol,len(frames)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=16);ap.add_argument('--codes',default='reports/reference-codes.txt');args=ap.parse_args()
 codes=Path(args.codes).read_text().splitlines()
 symbols=[('sh' if c.startswith('6') else 'bj' if c.startswith('9') else 'sz')+c for c in codes]
 ROOT.mkdir(parents=True,exist_ok=True)
 with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
  for i,result in enumerate(pool.map(fetch_symbol,symbols),1):
   if i%50==0 or isinstance(result[1],str) and result[1]!='cached': print(i,result,flush=True)
if __name__=='__main__':main()
