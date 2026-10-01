"""TDX strategy formulas: parse the formula text and evaluate it over bound data.

The engine knows the TDX expression syntax and nothing about any strategy.
Indicators come from chao.indicators; market data is bound by bind().
"""
import ast
import re
from typing import Any, List, NamedTuple, Tuple
import numpy as np
import pandas as pd
from chao.market import MissingInput
from chao.indicators import INDICATORS

# Output variables every strategy formula must define (TDX identifiers).
BUY_KEY, SELL_KEY = '买入条件', '卖出条件'
ASSIGNMENT = re.compile(r'([A-Za-z][A-Za-z0-9]*|买入条件|卖出条件)\s*:=\s*([^;]+);')
HEADER = re.compile(r'\{策略[0-9]+-([^}]+)\}')


class Strategy(NamedTuple):
    id: int
    name: str
    assignments: List[Tuple[str, str]]   # (variable, TDX expression text)
    program: List[Tuple[str, Any]]       # (variable, parsed expression), parsed once


def parse_strategy(sid, text):
    """Parse one strategy formula file."""
    header = HEADER.search(text)
    body = re.sub(r'\{[^}]*\}', '', text)
    assignments = [(k, e.strip()) for k, e in ASSIGNMENT.findall(body)]
    keys = [k for k, _ in assignments]
    if BUY_KEY not in keys or SELL_KEY not in keys:
        raise ValueError('strategy {} must define {} and {}'.format(sid, BUY_KEY, SELL_KEY))
    program = [(k, parse(e)) for k, e in assignments]
    return Strategy(sid, header.group(1) if header else str(sid), assignments, program)


INDEX_REF = re.compile(r'"([0-9]+)\$C"')


def referenced_indices(strategy):
    """Index codes a formula reads explicitly, e.g. "399006$C"."""
    return {code for _, e in strategy.assignments for code in INDEX_REF.findall(e)}


def load_strategies(files):
    """{file name: formula text} -> {id: Strategy}; the id is the name prefix."""
    result = {}
    for file_name, text in files.items():
        sid = int(file_name.split('-', 1)[0])
        if sid in result:
            raise ValueError('duplicate strategy id {}'.format(sid))
        result[sid] = parse_strategy(sid, text)
    return result


def parse(expression):
    expression = INDEX_REF.sub(r'INDEX("\1")', expression)
    expression = re.sub(r'\bAND\b', 'and', expression)
    expression = re.sub(r'\bOR\b', 'or', expression)
    expression = re.sub(r'(?<![<>=!])=(?!=)', '==', expression)
    return ast.parse(expression.strip(), mode='eval').body


def literal(node):
    """Literal value of a number or string node; Python 3.6 has no ast.Constant."""
    kind = type(node).__name__
    if kind == 'Constant' or kind == 'NameConstant':
        return node.value
    if kind == 'Num':
        return node.n
    if kind == 'Str':
        return node.s
    raise ValueError('not a literal')


# Names whose values depend only on the stock's market data. A call that
# only refers to these (e.g. MA(CLOSE,20)) is computed once per stock and
# shared across strategies.
DATA_NAMES = frozenset(['CLOSE', 'HIGH', 'LOW', 'AMO', 'INDEXC', 'INDEX', 'FINANCE', 'NAMELIKE'])


def memo_key(node):
    """ast.dump of a call that only reads market data, else None; kept on the node."""
    if not hasattr(node, 'chao_memo_key'):
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        node.chao_memo_key = ast.dump(node) if names <= DATA_NAMES | set(INDICATORS) else None
    return node.chao_memo_key


def evaluate(node, env, memo=None):
    if type(node).__name__ in ('Constant', 'Num', 'Str', 'NameConstant'):
        return literal(node)
    if isinstance(node, ast.Name): return env[node.id]
    if isinstance(node, ast.Call):
        if not isinstance(node.func,ast.Name) or node.keywords:
            raise ValueError('Unsupported formula call')
        key = memo_key(node) if memo is not None else None
        if key is not None and key in memo:
            return memo[key]
        value = env[node.func.id](*(evaluate(x,env,memo) for x in node.args))
        if key is not None:
            memo[key] = value
        return value
    if isinstance(node, ast.BoolOp):
        operands = [evaluate(x,env,memo) for x in node.values]
        result = operands[0]
        for operand in operands[1:]:
            result = result & operand if isinstance(node.op,ast.And) else result | operand
        return result
    if isinstance(node, ast.BinOp):
        return arithmetic(node.op, values_of(evaluate(node.left,env,memo)), values_of(evaluate(node.right,env,memo)))
    if isinstance(node,ast.Compare):
        # Compare plain arrays: identical results, far less pandas overhead.
        a=values_of(evaluate(node.left,env,memo)); result=True
        for op,right in zip(node.ops,node.comparators):
            b=values_of(evaluate(right,env,memo))
            # Same as np.isclose(a, b, rtol=0, atol=1e-10), without its overhead.
            with np.errstate(invalid='ignore'):
                close = (a == b) | (np.abs(np.subtract(a, b)) <= 1e-10)
            # Decimal bars and averages lose equality at the last binary bit;
            # every operator treats values within 1e-10 as equal.
            if isinstance(op,ast.Lt): value=np.logical_and(a<b, np.logical_not(close))
            elif isinstance(op,ast.LtE): value=(a<=b) | close
            elif isinstance(op,ast.Gt): value=np.logical_and(a>b, np.logical_not(close))
            elif isinstance(op,ast.GtE): value=(a>=b) | close
            elif isinstance(op,ast.Eq): value=np.logical_or(a==b, close)
            else: raise ValueError('Unsupported comparison')
            result=result & value; a=b
        return result
    raise ValueError('Unsupported formula AST: {}'.format(ast.dump(node)))


def arithmetic(op, a, b):
    """Element-wise IEEE arithmetic on plain arrays, as pandas would do."""
    with np.errstate(divide='ignore', invalid='ignore'):
        if isinstance(op, ast.Add): return a + b
        if isinstance(op, ast.Sub): return a - b
        if isinstance(op, ast.Mult): return a * b
        if isinstance(op, ast.Div): return a / b
    raise ValueError('Unsupported operator {}'.format(type(op).__name__))


def values_of(x):
    return x.values if isinstance(x, (pd.Series, pd.DataFrame)) else x


def reads(strategy):
    """Names a strategy's formulas refer to (e.g. NAMELIKE, FINANCE)."""
    return {n.id for _, node in strategy.program for n in ast.walk(node) if isinstance(n, ast.Name)}


def bind(close, high, low, amo, indexc, index_of, names, shares):
    """Formula names over a panel: one column per stock, rows are each stock's
    own bars. names holds a name or None per stock; shares is a panel with NaN
    where the share capital is unknown. A stock without a name never passes
    the ST exclusion; callers report it (see reads())."""
    def finance(field):
        if field != 1: raise ValueError('Unknown FINANCE field {}'.format(field))
        return shares
    def namelike(prefix):
        return np.array([float(n.startswith(prefix)) if n else np.nan for n in names])
    return dict(FINANCE=finance, INDEX=index_of, NAMELIKE=namelike,
                CLOSE=close, HIGH=high, LOW=low, AMO=amo, INDEXC=indexc)


def formula_environment(strategy, bound, memo=None):
    env = dict(INDICATORS)
    env.update(bound)
    for key, node in strategy.program:
        env[key] = evaluate(node, env, memo)
    return env


def signals(strategy, bound, shape, memo=None):
    """(buy, sell) boolean arrays of the given (bars, stocks) shape."""
    env = formula_environment(strategy, bound, memo)
    def flags(key):
        value = np.asarray(values_of(env[key]))
        if value.dtype != bool:
            raise ValueError('{} of strategy {} must be a condition'.format(key, strategy.id))
        return np.broadcast_to(value, shape)
    return flags(BUY_KEY), flags(SELL_KEY)
