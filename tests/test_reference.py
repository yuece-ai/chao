from chao.reference import read_universe


def test_universe_lists_every_scanned_stock_with_its_name(tmp_path):
    header = '品种代码\t品种名称\t盈利数\t总次数\n'
    (tmp_path / '策略1-测试.txt').write_text(header + '------\t综合统计\t1\t2\n600000\t浦发银行\t0\t0\n',
                                         encoding='utf-8-sig')
    (tmp_path / '策略1-测试-交易信号.txt').write_text('ignored', encoding='utf-8')
    (tmp_path / '策略源码.txt').write_text('ignored', encoding='utf-8')
    assert read_universe(tmp_path) == {1: {'600000': '浦发银行'}}
