# TDX 七策略回放与 QMT 迁移

## 背景

本项目把 `origin/策略源码.txt` 中的 7 个通达信交易公式迁移为 Python 回放程序，并使用 `cryptd` 已下载的通达信日线作为原始行情。前复权价格由 TDX 的 GBBQ 除权除息记录重建，结果与 `origin/` 目录下通达信导出的交易信号逐条对账。

最终目标是接入 QMT：7 个策略共用一个资金账户，信号出现后按统一的交易协调规则自动执行。当前阶段的首要验收目标仍是历史回放正确性，不能把未完成的历史对账直接用于自动下单。

## 数据与参数

- 原始日线：`/home/fikgol/data/tdx/cryptd-workspace/data/tdx/raw/vipdoc`
- GBBQ：`/home/fikgol/data/tdx/gbbq.json`
- 参考交易：`origin/*交易信号.txt`
- 公式源码：`origin/策略源码.txt`
- 7 个参考文件共 15,118 条交易事件，策略 1–7 的事件数分别为 720、1,594、1,792、1,846、5,370、3,196、600。

历史 TDX 对账应使用参考文件实际体现的参数：收盘价成交（无滑点），买入佣金 0.05%，卖出佣金 0.03%。用户此前给出的 QMT 参数（0.3% 滑点、万分之一佣金、最低 5 元）属于另一套实盘/模拟执行配置，不能与历史 TDX 对账配置混用。

## 已完成

1. 受限 AST 公式引擎已覆盖 7 个公式及 `MA/REF/HHV/LLV/FINANCE/NAMELIKE/INDEX`。
2. 已实现 GBBQ affine 前复权；GBBQ C1–C4 按 TDX 解码后的三位小数处理，调整后价格按 TDX 风格保留到分。
3. 已实现板块指数映射：普通沪市 `999999`、普通深市 `399001`、创业板 `399006`、科创板 `000688`、北交所 `899050`。
4. 回放支持多进程、独立股票状态、手续费和期末平盘。
5. 已定位到参考导出不是单一复权快照：例如策略 2/3 的 `601311` 在 2018-03-12 的参考价格为 8.22，而策略导出中的相邻日期和不同策略会体现不同快照/临界值。
6. 已验证“公式使用未四舍五入价格、成交使用四舍五入价格”会显著变差，不能作为生产路径。
7. `flake.nix` 开发环境和原有核心测试可用；`nix flake check --no-build` 已通过。

## 当前最佳基线

报告：`reports/parity/replay-report.json`，配置：`config.json`。[PARITY.md](PARITY.md)

| 策略 | 参考事件 | 匹配 | 缺失 | 多出 |
|---|---:|---:|---:|---:|
| 1 | 720 | 720 | 0 | 0 |
| 2 | 1,594 | 1,594 | 0 | 0 |
| 3 | 1,792 | 1,790 | 2 | 2 |
| 4 | 1,846 | 1,845 | 1 | 1 |
| 5 | 5,370 | 5,369 | 1 | 1 |
| 6 | 3,196 | 3,196 | 0 | 2 |
| 7 | 600 | 598 | 2 | 2 |
| **合计** | **15,118** | **15,112** | **6** | **8** |

## 当前未完成项

### QMT 接入前必须保留的边界

- QMT 的成交时点、实时价格、涨跌停、T+1、订单回报和撤单必须单独设计，不能为了历史 TDX 对齐而把同一根日线收盘价当成实盘可成交价。
- 7 个策略共用账户时，需要独立的信号排序、资金预占、重复信号幂等和订单回报核对层；当前尚未合入 QMT 接入代码。
- 实盘默认必须是 dry-run，只有明确配置和订单回报校验通过后才能启用真实下单。

## 常用命令

运行测试：

```sh
PYTHONPATH=. nix develop --command python -m pytest -q
nix flake check --no-build
```

运行当前历史基线：

```sh
PYTHONPATH=. nix develop --command python run_replay.py \
  --config config.json --reference-only
```

查看逐条差异：

```sh
python - <<'PY'
import json
x = json.load(open('reports/parity/residuals.json'))
for row in x:
    print(row['strategy'], row['code'], row['date'], row['direction'], row['layer'], row['role'], row.get('cause'))
PY
```
