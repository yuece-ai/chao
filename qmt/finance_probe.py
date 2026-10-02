#coding:gbk
# One-off check of QMT financial data reads: paste into a strategy file, press Run once, send the log.
# Not part of chao_strategy.py; delete it once the share-history read is settled.
FIELD = 'CAPITALSTRUCTURE.total_capital'
PROFIT = u'\u5229\u6da6\u8868.\u51c0\u5229\u6da6'  # the doc example's second field


def init(C):
    pass


def show(label, call):
    try:
        value = call()
        if hasattr(value, 'dropna'):
            known = value.dropna(how='all') if hasattr(value, 'columns') else value.dropna()
            print('fin %s: %s shape=%s non-NaN=%s tail=%r' % (label, type(value).__name__, getattr(value, 'shape', None),
                  getattr(known, 'shape', None), known.tail(2) if hasattr(known, 'tail') else known))
        else:
            print('fin %s: %s %r' % (label, type(value).__name__, value))
    except Exception as e:
        print('fin %s: error %r' % (label, e))


def handlebar(C):
    if not C.is_last_bar():
        return
    # 1. the official example, verbatim
    show('doc example', lambda: C.get_financial_data([FIELD, PROFIT], ['000001.SZ', '000002.SZ'], '20171209', '20231204',
                                                     report_type='report_time'))
    # 2. one stock, a real end date, both report types
    show('600000 announce', lambda: C.get_financial_data([FIELD], ['600000.SH'], '20200101', '20260930',
                                                         report_type='announce_time'))
    show('600000 report', lambda: C.get_financial_data([FIELD], ['600000.SH'], '20200101', '20260930',
                                                       report_type='report_time'))
    # 3. the call chao used (end 2099)
    show('600000 to 2099', lambda: C.get_financial_data([FIELD], ['600000.SH'], '19900101', '20991231',
                                                        report_type='announce_time'))
    # 4. raw records, no daily fill
    show('600000 raw', lambda: C.get_raw_financial_data([FIELD], ['600000.SH'], '20100101', '20260930',
                                                        report_type='announce_time'))
    # 5. usage 2: one value at the current bar
    show('600000 usage2', lambda: C.get_financial_data('CAPITALSTRUCTURE', 'total_capital', 'SH', '600000', C.barpos))
