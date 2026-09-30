import argparse, concurrent.futures as cf, json, re, time
from pathlib import Path
from urllib.request import Request,urlopen

def fetch(code,out):
 p=Path(out)/f'{code}.json';
 if p.exists():
  try:
   if len(json.loads(p.read_text()).get('data',[])) > 1: return code,'cached'
  except Exception: pass
 market='sh' if code.startswith('6') else 'sz' if not code.startswith('9') else 'bj'
 for i in range(4):
  try:
   u=f'https://finance.sina.com.cn/realstock/company/{market}{code}/qfq.js'
   b=urlopen(Request(u,headers={'User-Agent':'Mozilla/5.0'}),timeout=15).read().decode('utf-8')
   payload=b.split('=',1)[1].split('/*',1)[0].rstrip(';').strip()
   json.loads(payload)
   p.parent.mkdir(parents=True,exist_ok=True);p.write_text(payload);return code,'ok'
  except Exception as e:
   if i==3:return code,str(e)
   time.sleep(i+1)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--codes',required=True);ap.add_argument('--out',default='/home/fikgol/data/tdx/sina-factors');ap.add_argument('--workers',type=int,default=24);a=ap.parse_args();codes=Path(a.codes).read_text().split();Path(a.out).mkdir(parents=True,exist_ok=True)
 with cf.ThreadPoolExecutor(a.workers) as ex:
  for i,r in enumerate(ex.map(lambda c:fetch(c,a.out),codes),1):
   if i%100==0 or r[1] not in ('ok','cached'):print(i,r,flush=True)
if __name__=='__main__':main()
