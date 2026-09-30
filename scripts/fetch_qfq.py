#!/usr/bin/env python3
"""Fetch Eastmoney daily forward-adjusted bars for reference symbols.
The TDX raw bars remain the canonical source; this only supplies qfq prices.
"""
import argparse, csv, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FIELDS='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
def fetch(code, root, start, end, attempts=5):
    market='1' if code.startswith('6') else '0' if code.startswith(('0','3')) else '0'
    url='https://push2his.eastmoney.com/api/qt/stock/kline/get?'+urlencode({
      'secid':f'{market}.{code}','klt':'101','fqt':'1','beg':start,'end':end,
      'fields1':'f1,f2,f3,f4,f5,f6','fields2':FIELDS})
    for i in range(attempts):
        try:
            req=Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'})
            with urlopen(req,timeout=30) as resp: data=json.loads(resp.read())
            rows=(data.get('data') or {}).get('klines') or []
            if not rows: raise RuntimeError('empty kline response')
            out=Path(root)/f'{code}.csv'; out.parent.mkdir(parents=True,exist_ok=True)
            with out.open('w',newline='') as f:
                w=csv.writer(f); w.writerow(['date','open','close','high','low'])
                for row in rows:
                    vals=row.split(','); w.writerow(vals[:5])
            return code,len(rows),None
        except Exception as e:
            if i+1==attempts: return code,0,str(e)
            time.sleep(1.5*(i+1))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default='/home/fikgol/data/tdx/qfq'); ap.add_argument('--codes',required=True); ap.add_argument('--workers',type=int,default=8); ap.add_argument('--start',default='19900101'); ap.add_argument('--end',default='20260930'); args=ap.parse_args()
    codes=sorted(set(x.strip().upper().replace('.SH','').replace('.SZ','').replace('.BJ','') for x in Path(args.codes).read_text().splitlines() if x.strip()))
    result=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
      futures=[pool.submit(fetch,c,args.root,args.start,args.end) for c in codes]
      for f in as_completed(futures):
        result.append(f.result()); print(result[-1],flush=True)
    Path(args.root).mkdir(parents=True,exist_ok=True); Path(args.root,'fetch-report.json').write_text(json.dumps(result,indent=2))
if __name__=='__main__': main()
