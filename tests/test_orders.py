from chao.orders import Book, Holding, Order, lot_volume, plan_orders


def book(cash=100000.0, total=100000.0, holdings=None, owned=()):
    return Book(cash, total, holdings or {}, set(owned))


def test_lots_and_star_minimum():
    assert lot_volume('SZ000001', 10000, 9.9) == 1000
    assert lot_volume('SH688001', 3000, 10.0) == 300
    assert lot_volume('SH688001', 1999, 10.0) == 0   # 100 shares < STAR minimum 200
    assert lot_volume('SH688001', 2500, 10.0) == 200


def test_sells_only_owned_sellable_holdings():
    b = book(holdings={'SZ000001': Holding(1000, 1000), 'SZ000002': Holding(500, 500),
                       'SZ000003': Holding(800, 0)}, owned={'SZ000001', 'SZ000003'})
    signals = [(1, 'SZ000001', 'sell'), (2, 'SZ000001', 'sell'), (1, 'SZ000002', 'sell'), (1, 'SZ000003', 'sell')]
    # 000002 is the user's own holding; 000003 was bought today (T+1).
    assert plan_orders(signals, b, {}, 10, (1, 2)) == [Order('sell', 'SZ000001', 1000, 1)]


def test_buys_fill_free_slots_in_priority_then_symbol_order():
    b = book(cash=30000.0, total=100000.0, holdings={'SZ000009': Holding(100, 100)}, owned={'SZ000009'})
    signals = [(2, 'SZ000001', 'buy'), (1, 'SZ000003', 'buy'), (1, 'SZ000002', 'buy'), (1, 'SZ000009', 'buy')]
    prices = {'SZ000001': 10.0, 'SZ000002': 10.0, 'SZ000003': 10.0}
    # 3 free slots (000009 is held). Target 100000/4 = 25000; 6000 cash is left
    # after the first buy and 1000 after the second, too little for 000001.
    orders = plan_orders(signals, b, prices, 4, (1, 2))
    assert orders == [Order('buy', 'SZ000002', 2400, 1), Order('buy', 'SZ000003', 500, 1)]


def test_priority_resolves_conflicts():
    # Priority 6 > 5 > 4 > 3 > 2 > 1: strategy 5 takes the only slot,
    # and a stock signalled by 1 and 4 belongs to 4.
    b = book(cash=100000.0, total=100000.0)
    signals = [(1, 'SZ000001', 'buy'), (4, 'SZ000001', 'buy'), (5, 'SZ000009', 'buy')]
    prices = {'SZ000001': 10.0, 'SZ000009': 10.0}
    priority = (6, 5, 4, 3, 2, 1)
    assert plan_orders(signals, b, prices, 1, priority) == [Order('buy', 'SZ000009', 9900, 5)]
    assert plan_orders(signals, b, prices, 2, priority)[1] == Order('buy', 'SZ000001', 4900, 4)
