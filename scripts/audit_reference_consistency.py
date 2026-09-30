"""Audit whether the exported strategy files share one price snapshot.

This is a diagnostic only.  It never changes bars, signals, or replay output.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from chao.data import load_prices
from chao.reference import read_references


def exchange_symbol(code):
    return ("SH" if code.startswith("6") else "BJ" if code.startswith("9") else "SZ") + code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="reports/reference-consistency.json")
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_text())
    refs = read_references(config["reference_root"])

    summary = {}
    all_rows = []
    for sid, rows in sorted(refs.items()):
        dates = [r["date"] for r in rows]
        summary[str(sid)] = {
            "rows": len(rows),
            "symbols": len({r["code"] for r in rows}),
            "first_date": min(dates),
            "last_date": max(dates),
        }
        all_rows.extend((sid, r) for r in rows)

    # A key is one instrument, date and side.  A single shared daily snapshot
    # must give the same price for every strategy containing that key.
    by_key = defaultdict(list)
    for sid, row in all_rows:
        by_key[(row["code"], row["date"], row["direction"])].append(
            {"strategy": sid, "price": row["price"], "quantity": row["quantity"], "amount": row["amount"]}
        )
    conflicts = []
    for (code, date, direction), rows in sorted(by_key.items()):
        prices = {r["price"] for r in rows}
        if len(prices) > 1:
            conflicts.append({"code": code, "date": date, "direction": direction, "rows": rows})

    # Compare the reference price with the current official-GBBQ qfq close.
    closes = {}
    close_summary = {str(sid): {"rows": 0, "within_half_cent": 0, "different": 0} for sid in refs}
    for sid, row in all_rows:
        symbol = exchange_symbol(row["code"])
        if symbol not in closes:
            closes[symbol] = load_prices(config, symbol)["close"]
        close = closes[symbol].get(row["date"])
        if close is None:
            continue
        x = close_summary[str(sid)]
        x["rows"] += 1
        if abs(float(close) - row["price"]) < 0.005:
            x["within_half_cent"] += 1
        else:
            x["different"] += 1

    result = {
        "strategy_date_ranges": summary,
        "shared_key_count": len(by_key),
        "cross_strategy_price_conflict_count": len(conflicts),
        "cross_strategy_price_conflicts": conflicts,
        "reference_vs_current_qfq_close": close_summary,
        "interpretation": (
            "Different first/last signal dates can come from signal occurrence, but a date window "
            "cannot explain different prices for the same code/date/direction. Those conflicts "
            "require different adjustment snapshots or source exports."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ("shared_key_count", "cross_strategy_price_conflict_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
