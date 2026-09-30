# TDX parity audit

The default `config.json` uses one current official-GBBQ forward-adjusted
snapshot for all seven strategies.  The latest run with the TDX decimal
boundary rule matches 15,074 of 15,118 reference event keys (99.71%).

`config-reference-parity.json` demonstrates the historical-export mode.  It
uses the current snapshot for strategies 1–5 and 7, and an as-of 2026-09-11
snapshot for strategy 6.  That choice is evidence based: the reference file
has a strategy-6 row for 000155 on 2019-02-18 at 3.57, while the current qfq
close is 3.31.  GBBQ contains a 2026-09-22 cash-dividend event of 2.60 yuan
per 10 shares, which accounts exactly for the 0.26 difference.  This mode
matches 15,080 of 15,118 keys (99.7486%).

The remaining mismatches are listed in
`reports/reference-parity-mismatches.json`.  They are caused by the source
exports containing different adjustment snapshots and a small number of
borderline index/price values; no reference transaction is used to alter a
bar or signal.

Run the historical parity replay with:

```sh
PYTHONPATH=. nix develop --command python run_replay.py \
  --config config-reference-parity.json --reference-only --workers 48
```
