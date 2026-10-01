"""Read cryptd-acquired TDX files without changing the data provider."""
from pathlib import Path
import numpy as np
import pandas as pd
from chao.market import MissingInput

DTYPE = np.dtype([
    ('date', '<u4'), ('open', '<u4'), ('high', '<u4'), ('low', '<u4'),
    ('close', '<u4'), ('amount', '<f4'), ('volume', '<u4'), ('reserved', '<u4'),
])

def read_day(path):
    path = Path(path)
    if path.stat().st_size % 32:
        raise ValueError(f'Invalid TDX record size: {path}')
    rows = np.fromfile(path, dtype=DTYPE)
    frame = pd.DataFrame({name: rows[name] for name in DTYPE.names[:-1]})
    frame['date'] = pd.to_datetime(frame['date'].astype(str), format='%Y%m%d')
    for name in ('open', 'high', 'low', 'close'):
        frame[name] = frame[name] / 100.0
    if frame.date.duplicated().any():
        duplicate = frame[frame.date.duplicated(keep=False)]
        for _, group in duplicate.groupby('date'):
            if len(group.drop_duplicates()) != 1:
                raise ValueError(f'Conflicting duplicate dates: {path}')
        frame = frame.drop_duplicates('date')
    if not frame.date.is_monotonic_increasing:
        raise ValueError(f'Unsorted dates: {path}')
    return frame.set_index('date')


def symbol_path(root, symbol):
    market, code = symbol[:2].lower(), symbol[2:]
    return Path(root) / market / 'lday' / f'{market}{code}.day'


def equity_files(root):
    for market in ('sh', 'sz', 'bj'):
        for path in sorted((Path(root) / market / 'lday').glob('*.day')):
            code = path.stem[2:]
            if (market == 'sh' and code.startswith(('600', '601', '603', '605', '688', '689'))
                or market == 'sz' and code.startswith(('000', '001', '002', '003', '300', '301', '302'))
                or market == 'bj' and code.startswith(('43', '83', '87', '92'))):
                yield path.stem.upper(), path


def load_prices(raw_root, qfq_root, symbol):
    frame = read_day(symbol_path(raw_root, symbol))
    qpath = Path(qfq_root) / f'{symbol}.csv'
    if not qpath.exists():
        raise MissingInput(f'Missing exchange-qualified adjusted prices: {qpath}')
    qfq = pd.read_csv(qpath, parse_dates=['date']).set_index('date')
    if qfq.empty or not qfq.index.isin(frame.index).all():
        raise MissingInput(f'Adjusted dates must exist in raw data: {symbol}')
    frame = frame.loc[qfq.index].copy()
    for key in ('open', 'high', 'low', 'close'):
        values = qfq[key]
        if values.isna().any() or not np.isfinite(values).all() :
            raise ValueError(f'Invalid qfq prices: {symbol}/{key}')
        frame[key] = values
    return frame
