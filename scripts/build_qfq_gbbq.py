"""Build TDX-style forward-adjusted bars from the official GBBQ dump.

GBBQ category 1 is XRXD: C1 dividend (yuan/10 shares), C2 allotment
price, C3 bonus shares and C4 allotment shares (both shares/10 shares).
The TDX qfq transform is affine, so cash dividends must not be represented
by a multiplier alone.
"""
import argparse, json
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import numpy as np
import pandas as pd
from chao.data import read_day, symbol_path

def action_value(value):
    """TDX rounds decoded GBBQ float32 action fields to fen first."""
    value = Decimal(str(np.float32(value))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(value)

def qfq(raw, records):
    events=[]
    for x in records:
        if int(x.get("Category", 0)) != 1: continue
        d=pd.Timestamp(x["Date"]).tz_localize(None)
        c1,c2,c3,c4=(action_value(x[k]) for k in ("C1","C2","C3","C4"))
        m=(10.0+c3+c4)/10.0
        c=(c1-c4*c2)/10.0
        if m > 0: events.append((d,m,c))
    events.sort()
    latest=raw.index[-1]
    events=[x for x in events if x[0] <= latest]
    a,b=1.0,0.0; i=len(events)-1; coeff=[]
    for d in raw.index[::-1]:
        while i >= 0 and events[i][0] > d:
            _,m,c=events[i]; a=a/m; b=b-a*c; i-=1
        coeff.append((a,b))
    coeff=np.asarray(coeff[::-1])
    out=raw.copy()
    for k in ("open","high","low","close"):
        # TDX rounds adjusted prices to fen using half-up for positive prices.
        # The desktop path carries decoded daily values as float32 before the
        # fen rounding; retaining that step avoids 10.705 becoming 10.70 due
        # to a binary value of 10.704999999999998.
        adjusted=np.asarray(out[k].to_numpy()*coeff[:,0]+coeff[:,1],dtype=np.float32)
        out[k]=np.where(adjusted >= 0,
                        np.floor(adjusted*100.0+0.5),
                        -np.floor(-adjusted*100.0+0.5))/100.0
    return out, coeff

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--raw-root",required=True); ap.add_argument("--gbbq",required=True); ap.add_argument("--out",required=True); ap.add_argument("--codes",default="")
    a=ap.parse_args(); data=json.loads(Path(a.gbbq).read_text()); by={}
    for x in data: by.setdefault(x["Code"],[]).append(x)
    codes=[x for x in a.codes.split(',') if x] if a.codes else sorted(by)
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True); stats=[]
    for code in codes:
        market='SH' if code.startswith('6') else 'BJ' if code.startswith('9') else 'SZ'
        p=symbol_path(a.raw_root,market+code)
        if not p.exists(): stats.append({'code':code,'status':'missing_raw'}); continue
        raw=read_day(p); q,_=qfq(raw,by.get(code,[])); q[["open","high","low","close"]].to_csv(out/f'{code}.csv',index_label='date')
        stats.append({'code':code,'status':'ok','events':sum(int(x.get('Category',0))==1 for x in by.get(code,[]))})
    (out/'factor-report.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)); print(json.dumps({'ok':sum(x['status']=='ok' for x in stats),'total':len(stats)}))
if __name__=='__main__': main()
