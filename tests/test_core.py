import pandas as pd
from chao.formulas import evaluate,parse,MA,REF,HHV,LLV,strategies
from chao.reference import score
from chao.replay import replay

def test_boolean_precedence():
 x=pd.Series([1,2,3]); result=evaluate(parse('X>1 AND X<3 OR X=1'),{'X':x})
 assert result.tolist()==[True,True,False]

def test_decimal_boundary_matches_tdx_tick_precision():
 assert bool(evaluate(parse('X<=Y'), {'X': 5.29, 'Y': 5.289999999999999}))

def test_functions():
 x=pd.Series([1.,3.,2.]); assert MA(x,2).iloc[-1]==2.5
 assert REF(x,1).iloc[-1]==3; assert HHV(x,2).iloc[-1]==3
 assert LLV(x,2).iloc[-1]==2

def test_source_complete():
 s=strategies('origin/策略源码.txt'); assert set(s)==set(range(1,8))
 assert all(v['assignments'][-1][1]=='CLOSE<=MA(CLOSE,20)' for v in s.values())

def test_score_keeps_codes():
 rows=[{'code':'000001','date':'2020-01-01','direction':'买开','price':1}, {'code':'000002','date':'2020-01-01','direction':'买开','price':1}]
 assert score(rows,rows)['matched']==2

def test_slippage_and_commission():
 dates=pd.date_range('2020-01-01',periods=2)
 frame=pd.DataFrame({'close':[10.,11.]},index=dates)
 sig=pd.DataFrame({'buy':[True,False],'sell':[False,True]},index=dates)
 cfg={'initial_cash':1000000,'commission_rate':.0001,'minimum_commission':5,'slippage':.003,'start':'2020-01-01','end':'2020-01-02'}
 events,summary=replay(frame,sig,cfg)
 assert events[0]['price']==10.03
 assert events[1]['price']==11*.997
 assert abs(events[0]['fee']-events[0]['amount']*.0001)<1e-7
 assert summary['closed_trades']==1
