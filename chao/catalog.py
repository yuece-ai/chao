"""The strategy formula files under strategies/, keyed by file name.

Each file is one TDX formula (e.g. 1-range-breakout-outperform.tdx) that can
be edited or pasted back into TDX. The QMT bundle embeds these files.
"""
from pathlib import Path

STRATEGY_DIR = Path(__file__).resolve().parent.parent / 'strategies'


def strategy_files():
    return {p.name: p.read_text(encoding='utf-8') for p in sorted(STRATEGY_DIR.glob('*.tdx'))}
