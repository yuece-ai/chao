"""TDX GBBQ records (ex-rights and share-capital changes), keyed by stock code.

GBBQ codes are not exchange-qualified; they only describe stocks, never the
formula indices.
"""
import json
from collections import defaultdict
from pathlib import Path

SHARE_CAPITAL = 5  # C4: total shares after the change, in 10,000 shares


def load_gbbq(path):
    records = defaultdict(list)
    for row in json.loads(Path(path).read_text()):
        records[row['Code']].append(row)
    return dict(records)


def total_shares(records):
    """Historical FINANCE(1) steps as {YYYY-MM-DD: total shares / 10,000}.

    The TDX exports evaluate FINANCE(1) with the share capital in force on
    each bar, not today's value: 64 reference buys would be blocked by the
    4e9-share filter if the current value were used.
    """
    return {row['Date'][:10]: row['C4'] for row in records if int(row['Category']) == SHARE_CAPITAL}
