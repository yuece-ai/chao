import pandas as pd
import re
from pathlib import Path
from chao.catalog import strategy_files
from chao.formulas import ASSIGNMENT,evaluate,load_strategies,parse
from chao.indicators import MA,REF,HHV,LLV
from chao.reference import cents, score
from chao.replay import ReplaySpec, replay

def test_boolean_precedence():
 x=pd.Series([1,2,3]); result=evaluate(parse('X>1 AND X<3 OR X=1'),{'X':x})
 assert result.tolist()==[True,True,False]

def test_decimal_boundary_matches_tdx_tick_precision():
 assert bool(evaluate(parse('X<=Y'), {'X': 5.29, 'Y': 5.289999999999999}))

def test_functions():
 x=pd.Series([1.,3.,2.]); assert MA(x,2).iloc[-1]==2.5
 assert REF(x,1).iloc[-1]==3; assert HHV(x,2).iloc[-1]==3
 assert LLV(x,2).iloc[-1]==2

def test_strategy_files_match_the_tdx_export():
 # The per-strategy files must stay identical to the user's TDX source,
 # which is what the parity in PARITY.md was measured against.
 text=re.sub(r'\{[^}]*\}','',Path('origin/策略源码.txt').read_text(encoding='utf-8'))
 heads=list(re.finditer(r'策略([1-7])-',text))
 origin={int(h.group(1)):[(k,e.strip()) for k,e in ASSIGNMENT.findall(text[h.end():heads[i+1].start() if i+1<len(heads) else len(text)])] for i,h in enumerate(heads)}
 loaded=load_strategies(strategy_files())
 assert {sid:s.assignments for sid,s in loaded.items()}==origin

def test_score_keeps_codes():
 base={'date':'2020-01-01','direction':'买开','price':1.0,'quantity':10,'amount':10.0,'fee':0.01,'profit':0.0,'cash':9.99}
 rows=[{**base,'code':'000001'},{**base,'code':'000002'}]
 assert score(rows,rows)['matched']==2

def two_bar_replay(buy_close, sell_close):
 dates=pd.date_range('2020-01-01',periods=2)
 frame=pd.DataFrame({'close':[buy_close,sell_close]},index=dates)
 sig=pd.DataFrame({'buy':[True,False],'sell':[False,True]},index=dates)
 spec=ReplaySpec('2020-01-01','2020-01-02',1000000.0,0.0005,0.0003)
 return replay(frame,sig,spec)

def test_ledger_reproduces_tdx_export_row():
 # Strategy 1, 000006: 2015-03-20 buy at 6.06, 2015-05-05 sell at 9.41.
 events,summary=two_bar_replay(6.06,9.41)
 fields=[{k:(e[k] if k=='quantity' else cents(e[k])) for k in ('quantity','amount','fee','profit','cash')} for e in events]
 assert fields==[{'quantity':164934,'amount':999500.00,'fee':499.75,'profit':0.0,'cash':0.25},
                 {'quantity':164934,'amount':1552028.88,'fee':465.61,'profit':552528.88,'cash':1551563.50}]
 assert summary['closed_trades']==1

def test_ledger_clamps_fractional_overdraft_to_zero():
 # Strategy 2, 000839 on 2024-04-29: TDX buys 480529 shares and shows 0.00 cash.
 events,_=two_bar_replay(2.08,2.08)
 assert events[0]['quantity']==480529 and events[0]['cash']==0.0

def test_score_reports_accounting_fields():
 row={'code':'000001','date':'2020-01-01','direction':'买开','price':1.0,'quantity':10,'amount':10.0,'fee':0.01,'profit':0.0,'cash':9.99}
 result=score([row],[{**row,'fee':0.0149}])
 assert result['matched']==1 and result['accounting_matched']==1
 result=score([row],[{**row,'quantity':11}])
 assert result['accounting_matched']==0 and result['accounting_mismatches'][0]['fields'][0]['field']=='quantity'

def test_buy_and_sell_on_the_same_bar():
 # Strategy 5, 300475 on 2020-02-17: both conditions hold, TDX buys then sells.
 dates=pd.date_range('2020-01-01',periods=2)
 frame=pd.DataFrame({'close':[7.61,7.0]},index=dates)
 sig=pd.DataFrame({'buy':[True,False],'sell':[True,True]},index=dates)
 events,_=replay(frame,sig,ReplaySpec('2020-01-01','2020-01-02',1000000.0,0.0005,0.0003))
 assert [(e['date'],e['direction']) for e in events]==[('2020-01-01','买开'),('2020-01-01','卖平')]
