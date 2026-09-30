"""Independent-symbol TDX replay, with explicit execution parameters."""
import math
import pandas as pd


def replay(frame, signals, config):
    cash=float(config['initial_cash']); quantity=0; entry_cost=0; events=[]; curve=[]
    for date, row in frame.loc[config['start']:config['end']].iterrows():
        sig=signals.loc[date]; direction=None
        if quantity and sig.sell:
            direction='卖平'; price=row.close*(1-config['slippage'])
            gross=quantity*price
            fee=max(gross*config['commission_rate'],config['minimum_commission'])
            profit=gross-fee-entry_cost; cash+=gross-fee
            amount=quantity; quantity=0
        elif not quantity and sig.buy:
            direction='买开'; price=row.close*(1+config['slippage'])
            amount=math.floor((cash-config['minimum_commission'])/(price*(1+config['commission_rate'])))
            if amount<=0: continue
            gross=amount*price; fee=max(gross*config['commission_rate'],config['minimum_commission'])
            if gross+fee>cash: continue
            quantity=amount; cash-=gross+fee; entry_cost=gross+fee; profit=0
        if direction:
            events.append({'date':date.strftime('%Y-%m-%d'),'direction':direction,
                           'price':price,'quantity':amount,'amount':gross,'fee':fee,
                           'profit':profit,'cash':cash})
        curve.append((date,cash+quantity*row.close))
    if quantity and config.get('terminal_policy') == 'flat' and len(frame.loc[config['start']:config['end']]):
        date=frame.loc[config['start']:config['end']].index[-1]; row=frame.loc[date]
        price=row.close*(1-config['slippage']); gross=quantity*price
        fee=max(gross*config['commission_rate'],config['minimum_commission'])
        profit=gross-fee-entry_cost; cash+=gross-fee
        events.append({'date':date.strftime('%Y-%m-%d'),'direction':'平盘','price':price,
                       'quantity':quantity,'amount':gross,'fee':fee,'profit':profit,'cash':cash})
        quantity=0
    equity=pd.Series(dict(curve),dtype=float)
    sells=[e for e in events if e['direction']=='卖平']
    summary={'closed_trades':len(sells),'profitable_trades':sum(e['profit']>0 for e in sells),
             'net_profit':float(equity.iloc[-1]-config['initial_cash']) if len(equity) else 0,
             'fees':sum(e['fee'] for e in events),'open_quantity':quantity,
             'max_drawdown':float((equity.cummax()-equity).max()) if len(equity) else 0}
    return events,summary
