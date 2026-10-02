"""Trade lists and summaries in the layout of the TDX signal exports.

The QMT backtest replays every (strategy, stock) pair with the TDX ledger
(chao.replay) and writes the trades in the same columns as the user's TDX
exports, so the two can be compared row by row.
"""
import os
from decimal import Decimal, ROUND_HALF_UP

# Column names of the TDX export, kept verbatim so the files line up.
COLUMNS = ['品种代码', '品种名称', '时间', '信号', '价格(元)', '交易量(股/手)', '成交额(元)',
           '收益(元)', '手续费(元)', '可用资金(元)']


def cents(value):
    """A stored value rounded the way the export displays it."""
    return Decimal(repr(float(value))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def export_lines(events, names):
    """Tab-separated lines (header first) for one strategy's events."""
    lines = ['\t'.join(COLUMNS)]
    for e in sorted(events, key=lambda e: (e['code'], e['date'], e['direction'])):
        lines.append('\t'.join([e['code'], names.get(e['code']) or '', e['date'] + ' 00:00', e['direction'],
                                str(cents(e['price'])), str(int(e['quantity'])), str(cents(e['amount'])),
                                str(cents(e['profit'])), str(cents(e['fee'])), str(cents(e['cash']))]))
    return lines


def write_exports(directory, events_by_strategy, names, stamp):
    """One new file per strategy and run: strategy-<id>-signals-<stamp>.tsv
    (UTF-8). QMT lets a strategy create files but did not let a rerun
    replace them, so a run never writes over an earlier one."""
    if not os.path.isdir(directory):  # QMT's sandbox may forbid even an existing folder's mkdir
        os.makedirs(directory)
    paths = []
    for sid, events in sorted(events_by_strategy.items()):
        path = os.path.join(directory, 'strategy-{}-signals-{}.tsv'.format(sid, stamp))
        with open(path, 'w', encoding='utf-8') as out:
            out.write('\n'.join(export_lines(events, names)) + '\n')
        paths.append(path)
    return paths


def latest_exports(directory):
    """{strategy id: path of its newest strategy-<id>-signals-<stamp>.tsv}."""
    latest = {}
    for name in sorted(os.listdir(directory)):  # stamps sort by time
        parts = name.split('-')
        if name.endswith('.tsv') and len(parts) >= 3 and parts[0] == 'strategy' and parts[2].startswith('signals'):
            latest[int(parts[1])] = os.path.join(directory, name)
    return latest


def summary_line(sid, summaries):
    """Totals over one strategy's stocks, like the export's 综合统计 row."""
    closed = sum(s['closed_trades'] for s in summaries)
    wins = sum(s['profitable_trades'] for s in summaries)
    return 'strategy={} trades={} wins={} win_rate={:.2f}% net_profit={:.2f} fees={:.2f}'.format(
        sid, closed, wins, 100.0 * wins / closed if closed else 0.0,
        sum(s['net_profit'] for s in summaries), sum(s['fees'] for s in summaries))
