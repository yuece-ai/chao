"""Build qualified qfq files using the reference export's last date."""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from chao.data import read_day, symbol_path
from chao.reference import read_references
from scripts.build_qfq_gbbq import qfq
from scripts.build_qfq_exact import qfq_exact


G = {}


def initialize(raw_root, output, end, by_code, transform):
    G.update(raw=Path(raw_root), out=Path(output), end=end, by_code=by_code, transform=transform)


def build_one(code):
    symbol = ("SH" if code in ("999999", "000688") or code.startswith("6") else
              "BJ" if code == "899050" or code.startswith("9") else "SZ") + code
    symbols = [symbol]
    # 000688 is both the Shenzhen stock code and the Shanghai STAR-50 index.
    # Preserve both namespaces in an as-of root.
    if code == "000688":
        symbols.append("SZ000688")
    built = False
    for symbol in symbols:
        path = symbol_path(G["raw"], symbol)
        if not path.exists():
            continue
        frame = read_day(path).loc[:G["end"]]
        adjusted = G["transform"](frame, G["by_code"].get(code, []))
        if isinstance(adjusted, tuple):
            adjusted = adjusted[0]
        values = adjusted[["open", "high", "low", "close"]]
        values.to_csv(G["out"] / f"{symbol}.csv", index_label="date")
        if symbol == ("SH" if code in ("999999", "000688") else "BJ" if code == "899050" else "SZ") + code:
            if code not in ("399001", "399006", "999999", "899050", "000688"):
                values.to_csv(G["out"] / f"{code}.csv", index_label="date")
        built = True
    return built


def main():
    sid = int(sys.argv[1])
    config = json.loads(Path("config.json").read_text())
    refs = read_references(config["reference_root"])
    end = max(row["date"] for row in refs[sid])
    raw = Path(config["raw_root"])
    records = json.loads(Path("/home/fikgol/data/tdx/gbbq.json").read_text())
    by_code = {}
    for row in records:
        by_code.setdefault(row["Code"], []).append(row)
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(f"/home/fikgol/data/tdx/qfq-cutoff-s{sid}")
    out.mkdir(parents=True, exist_ok=True)
    codes = ({row["code"] for rows in refs.values() for row in rows}
             if len(sys.argv) > 2 and sys.argv[2] == "all"
             else {row["code"] for row in refs[sid]})
    codes.update(("399001", "399006", "999999", "899050", "000688"))
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else min(os.cpu_count() or 1, 48)
    transform = qfq_exact if '--exact' in sys.argv else qfq
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize,
                             initargs=(raw, out, end, by_code, transform)) as pool:
        present = sum(pool.map(build_one, sorted(codes), chunksize=max(1, len(codes) // (workers * 8))))
    print(json.dumps({"strategy": sid, "end": end, "workers": workers,
                      "built": present, "files": len(list(out.glob("*.csv")))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
