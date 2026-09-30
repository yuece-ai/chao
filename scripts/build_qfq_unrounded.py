import argparse,json
from pathlib import Path
import pandas as pd
from chao.data import read_day,symbol_path
from scripts.build_qfq_gbbq import qfq
p=argparse.ArgumentParser();p.add_argument('--raw-root',required=True);p.add_argument('--gbbq',required=True);p.add_argument('--out',required=True);a=p.parse_args();d=json.loads(Path(a.gbbq).read_text()); by={}
for x in d:by.setdefault(x['Code'],[]).append(x)
o=Path(a.out);o.mkdir(parents=True,exist_ok=True)
for code,rows in by.items():
 m='SH' if code.startswith('6') or code=='000688' else 'BJ' if code.startswith('9') else 'SZ'; p=symbol_path(a.raw_root,m+code)
 if not p.exists():continue
 raw=read_day(p); _,co=qfq(raw,rows); z=raw.copy()
 for k in ('open','high','low','close'):z[k]=raw[k].to_numpy()*co[:,0]+co[:,1]
 z[['open','high','low','close']].to_csv(o/f'{code}.csv',index_label='date')
print('built',len(list(o.glob('*.csv'))))
