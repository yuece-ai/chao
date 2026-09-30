# TDX parity

The replay is scored against the seven TDX signal exports in `origin/`
(15,118 trade rows). The replay never reads reference rows. They are used
for three things:
- scoring;
- choosing between global rules;
- selecting each export's adjustment snapshot date (below).

## Result

`run_replay.py --config config.json --reference-only`:

| Strategy | Reference rows | Signals matched | Missing | Extra | Accounting matched |
|---|---:|---:|---:|---:|---:|
| 1 | 720 | 720 | 0 | 0 | 720 |
| 2 | 1,594 | 1,594 | 0 | 0 | 1,591 |
| 3 | 1,792 | 1,790 | 2 | 2 | 1,785 |
| 4 | 1,846 | 1,845 | 1 | 1 | 1,842 |
| 5 | 5,370 | 5,369 | 1 | 1 | 5,352 |
| 6 | 3,196 | 3,196 | 0 | 2 | 3,189 |
| 7 | 600 | 598 | 2 | 2 | 590 |
| **Total** | **15,118** | **15,112 (99.96%)** | **6** | **8** | **15,069 of 15,112** |

A signal match means code, date and direction agree. An accounting match
means quantity, price, amount, fee, profit and available cash all agree
with the export at the displayed cent. This is not 100%. Every remaining
row traces back to one of 27 root causes listed below:
- 20 are float or display effects in the ledger or the adjusted prices;
- 5 are formula comparisons within 5e-6 of equality, sensitive to
  last-digit differences but not reproduced from our data;
- 2 are unexplained.

## Reproduced TDX rules

**Adjusted prices** (`chao/qfq.py`, built by `scripts/build_qfq.py`)
- Forward adjustment uses GBBQ category 1 only:
  `P' = (P - C1/10 + C2*C4/10) / (1 + (C3+C4)/10)`, composed exactly and
  rounded once, half away from zero, to the fen.
- GBBQ float32 fields are read at three decimals (1.799997 → 1.800).
- Each export only knew the ex-dates up to its snapshot date, and GBBQ also
  lists announced future ex-dates. The snapshot dates were selected by
  scanning candidate dates against the export's reference trade prices. The
  exact dividend amounts corroborate the choice:

  | Export | Snapshot used | Consistent range | Evidence |
  |---|---|---|---|
  | 1, 3, 4, 5, 7 | 2026-09-30 | ≥ 2026-09-30 | every price matches the current snapshot |
  | 2 | 2026-09-25 | 09-25 – 09-27 | 000062 differs by exactly the 2026-09-30 dividend (0.30) |
  | 6 | 2026-09-14 | 09-14 – 09-16 | includes the 09-14 events (300908 bonus); excludes 000155's 09-22 dividend (0.26) |

- These rules reproduce 15,115 of the 15,118 reference trade prices.

**Formulas** (`chao/formulas.py`)
- The formulas are evaluated in double precision, and every comparison operator treats values within 1e-10 as equal. Both float32 variants (window sums and running sums) matched fewer rows (15,087 and 15,093).
- INDEXC is the board index (`chao/data.py::board_index`):
  - `899050` for Beijing stocks
  - `000688` for SH688/689
  - `399006` for SZ300/301/302
  - `399001` for other SZ stocks
  - `999999` for other SH stocks
- `FINANCE(1)` is the historical total share capital, taken from GBBQ category 5. Using today's value would block 64 reference buys.

**Ledger** (`chao/replay.py`)
- Each symbol starts with 1,000,000 of cash.
- Fills happen at the bar close with no slippage. The buy fee is 0.05% and the sell fee is 0.03%, with no minimum fee.
- Money is kept in float32:
  - `qty = floor(cash / (f32(close) * f32(1.0005)))`
  - `amount = f32(qty*price)`
  - `fee = f32(amount*rate)`
  - `profit = f32(qty * f32(sell - buy))`
  - `cash_after_sell = f32(f32(f32(cash_before_buy - buy_fee) + profit) - sell_fee)`
  - a fractional overdraft on a buy is clamped to 0.
- On each bar the buy is checked before the sell, so one bar can open and close a position (3 reference cases). Open positions are flattened (`平盘`) on the last bar.

## Residual root causes

The classifier is `scripts/audit_residuals.py`. A cascade is a later row of the same strategy and stock, whose position or cash already differs because of the root row.

| Root cause | Roots | Cascades | Evidence |
|---|---:|---:|---|
| Formula comparison within 5e-6 of equality | 5 | 5 signal + 8 accounting | the closest comparisons have relative margins 1.0e-7 – 4.9e-6 (below) |
| Unexplained | 2 | 2 signal | margins ≥ 1.3e-4, so no rounding explains them (below) |
| Buy quantity off by one share | 9 | 11 | `cash/(f32(price)·f32(1.0005))` lies within 0.015 of an integer (e.g. 328782.9930 vs TDX 328783); TDX's internal float price lands on the other side |
| Adjusted price on a half-fen boundary | 3 | 1 | the exact qfq value is within 5e-5 of x.xx5 (300451 5.01496, 002865 12.294999) |
| Fee display on an exact half cent | 4 | 0 | float32 fee is x.xx5 (e.g. 303.455017); TDX shows the lower cent, while cash matches |
| Cash differs by one float32 step | 4 | 3 | 0.0625 – 0.125 on a cash value above 1,000,000 |

Signal roots:

| Strategy | Code | Date | Row | Closest comparison | Our values |
|---|---|---|---|---|---|
| 3 | 300125 | 2014-06-13 | missing buy | `MA(C,120) > MA(C,250)*1.075` | 6.544083 vs 6.544084 |
| 3 | 300160 | 2012-05-07 | extra buy | `MA(C,20) > MA(C,50)*0.95` | 2.797000 vs 2.796990 |
| 4 | 600249 | 2013-08-02 | extra buy (TDX bought 08-05) | `C/REF(C,10) < INDEXC/REF(INDEXC,10)` | 1.018450 vs 1.018453 |
| 5 | 600977 | 2024-09-25 | extra buy (TDX bought 09-26) | same | 1.058402 vs 1.058403 |
| 7 | 920964 | 2023-05-26 | extra buy | same | 1.015974 vs 1.015979 |
| 6 | 300096 | 2023-01-30 | extra buy | `MA(C,50) > REF(MA(C,50),1)` | margin 1.3e-4 |
| 7 | 920580 | 2026-05-13 | missing buy | `C/REF(C,10) < INDEXC/REF(INDEXC,10)` | 1.0697 vs 1.0498 |

The last two rows are unexplained:
- **920580:** 899050 is the right INDEXC here; it satisfies this condition for the other 299 of 300 strategy-7 reference buys. Dropping any one bar between 2026-04-24 and 05-12 reproduces both the 05-13 buy and the 05-21 sell. That fits a TDX bar history with one bar fewer, but we cannot identify the bar or verify it.
- **300096:** only strategy 3 exports this stock, so no reference price on or near 2023-01-30 can be checked.

The five boundary rows are consistent with last-digit differences in TDX's inputs, but the rules above do not reproduce them.

Rejected hypotheses:
- float32 formula evaluation (above);
- unrounded adjusted prices: 000006's exact value is 6.059, but TDX's amount 999,500.00 requires 6.06;
- today's FINANCE(1) value;
- per-export snapshots for exports 1, 3, 4, 5 and 7.

## Reproduce

```sh
for d in 2026-09-30 2026-09-25 2026-09-14; do
  PYTHONPATH=. nix develop --command python scripts/build_qfq.py \
    --raw-root /home/fikgol/data/tdx/cryptd-workspace/data/tdx/raw/vipdoc \
    --gbbq /home/fikgol/data/tdx/gbbq.json --as-of $d \
    --out /home/fikgol/data/tdx/qfq/asof-$d
done
PYTHONPATH=. nix develop --command python run_replay.py --config config.json --reference-only --workers 48
PYTHONPATH=. nix develop --command python scripts/trace_mismatches.py --config config.json \
  --reports reports/parity/replay-report.json --out reports/parity/mismatch-trace.json
PYTHONPATH=. nix develop --command python scripts/audit_residuals.py \
  --report reports/parity/replay-report.json --trace reports/parity/mismatch-trace.json \
  --out reports/parity/residuals.json
```

## Known limits

- `gbbq.json` is a JSON decode of TDX's GBBQ file, and its producer is not in this repository.
- `NAMELIKE` only knows the names in the reference exports. For any other stock the name is unknown, so the ST filter rejects it, and a full-universe run (without `--reference-only`) never buys those stocks.
- The ledger reproduces TDX's historical backtest. It is not an execution model for live trading.
