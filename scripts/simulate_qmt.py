#!/usr/bin/env python3
"""Run the bundled QMT strategy's backtest on Linux and score it against the
TDX exports.

The bundle is built in memory from the current sources and driven the way
the QMT client drives a backtest: init, then handlebar on every bar. The data
is TDX's, served in QMT's shapes by the fake client; the account fills every
mirrored order at the day's close and applies T+1. The trade lists the
strategy writes are compared with origin/ inside [start, end].
"""
import argparse, importlib.util, io, tempfile, time
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from chao.gbbq import load_gbbq
from chao.market import equity_symbol
from chao.qmt_source import to_qmt
from chao.reference import read_references, score
from scripts.bundle_qmt import bundle
from scripts.compare_tdx_report import read_report
from tests.qmt_fake import Obj
from tests.test_local_data import GBBQ, tdx_as_qmt


class SimAccount:
    """One stock account: fills every order at the day's close, T+1 sells."""

    def __init__(self, cash, buy_fee, sell_fee):
        self.cash, self.buy_fee, self.sell_fee = cash, buy_fee, sell_fee
        self.positions = {}          # qmt code -> [volume, sellable]
        self.fills, self.rejected = [], []
        self.price = None            # code -> today's close, set by the driver

    def new_day(self):
        for p in self.positions.values():
            p[1] = p[0]

    def get_trade_detail_data(self, account_id, account_type, kind, strategy_name=None):
        if kind == 'ACCOUNT':
            value = sum(v[0] * (self.price(c) or 0.0) for c, v in self.positions.items())
            return [Obj(m_dAvailable=self.cash, m_dBalance=self.cash + value)]
        if kind == 'POSITION':
            return [Obj(m_strExchangeID=c[-2:], m_strInstrumentID=c[:6], m_nVolume=v[0], m_nCanUseVolume=v[1])
                    for c, v in self.positions.items() if v[0] > 0]
        return []

    def passorder(self, op, order_type, account_id, code, pr_type, price, volume, name, quick, remark, C):
        fill = self.price(code)
        position = self.positions.setdefault(code, [0, 0])
        if op == 23:
            cost = None if fill is None else volume * fill * (1 + self.buy_fee)
            if cost is None or cost > self.cash:
                self.rejected.append((code, 'buy: no price or cash'))
                return
            self.cash -= cost
            position[0] += volume
        else:
            if fill is None or volume > position[1]:
                self.rejected.append((code, 'sell: T+1 or no shares'))
                return
            self.cash += volume * fill * (1 - self.sell_fee)
            position[0] -= volume
            position[1] -= volume
        self.fills.append((code, op, volume, fill))


def load_bundle(config):
    path = Path(tempfile.mkdtemp()) / 'chao_strategy.py'
    path.write_bytes(bundle('simulation', config).encode('ascii'))
    spec = importlib.util.spec_from_file_location('chao_strategy', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def close_on(C, code, day):
    frame = C.bars.get(code)
    return None if frame is None or day not in frame.index else float(frame.loc[day, 'close'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True, help='YYYY-MM-DD')
    ap.add_argument('--end', required=True, help='YYYY-MM-DD')
    ap.add_argument('--report', help='keep the trade lists in this directory (default: a temporary one)')
    args = ap.parse_args()
    refs = read_references('origin')
    names = {r['code']: r['name'] for rows in refs.values() for r in rows}
    symbols = sorted(equity_symbol(c) for c in names if not equity_symbol(c).startswith('BJ'))
    report = Path(args.report) if args.report else Path(tempfile.mkdtemp())
    module = load_bundle({'account_id': 'testS', 'report_path': str(report)})
    C = tdx_as_qmt(symbols, load_gbbq(GBBQ))
    C.names.update({to_qmt(s): names[s[2:]] for s in symbols})
    C.sectors = {'沪深A股': [to_qmt(s) for s in symbols]}
    index = C.bars['000001.SH']
    days = [d for d in index.index if args.start.replace('-', '') <= d <= args.end.replace('-', '')]
    C.do_back_test, C.bar_dates = True, list(index.index)
    C.start, C.end = args.start + ' 00:00:00', args.end + ' 15:00:00'
    account = SimAccount(1e12, 0.0005, 0.0003)
    module.passorder, module.get_trade_detail_data = account.passorder, account.get_trade_detail_data
    log = io.StringIO()
    started = time.time()
    with redirect_stdout(log):
        module.init(C)
        for day in days:
            account.new_day()
            account.price = lambda code, day=day: close_on(C, code, day)
            C.barpos = C.bar_dates.index(day)
            module.handlebar(C)
    lines = log.getvalue().splitlines()
    print('backtest {}..{}: {} bars in {:.0f}s, fills {}, rejected {}'.format(
        args.start, args.end, len(days), time.time() - started, len(account.fills),
        Counter(reason for _, reason in account.rejected)))
    for line in lines:
        if 'chao: tdx' in line or 'same bar' in line or 'warning' in line:
            print('  ' + line)
    inside = lambda rows: [r for r in rows if args.start <= r['date'] <= args.end]
    for path in sorted(report.glob('strategy-*-signals.tsv')):
        sid = int(path.stem.split('-')[1])
        s = score(inside(refs.get(sid, [])), inside(read_report(path)))
        print('  strategy {}: TDX rows {} matched {} accounting {}'.format(
            sid, s['expected'], s['matched'], s['accounting_matched']))


if __name__ == '__main__':
    main()
