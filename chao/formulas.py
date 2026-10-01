"""TDX strategy formulas: parse the formula text and evaluate it over bound data.

The engine knows the TDX expression syntax and nothing about any strategy.
Indicators come from chao.indicators; market data is bound by bind().
"""
import ast
import re
from typing import List, NamedTuple, Tuple
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
    assignments: List[Tuple[str, str]]


def parse_strategy(sid, text):
    """Parse one strategy formula file."""
    header = HEADER.search(text)
    body = re.sub(r'\{[^}]*\}', '', text)
    assignments = [(k, e.strip()) for k, e in ASSIGNMENT.findall(body)]
    keys = [k for k, _ in assignments]
    if BUY_KEY not in keys or SELL_KEY not in keys:
        raise ValueError('strategy {} must define {} and {}'.format(sid, BUY_KEY, SELL_KEY))
    return Strategy(sid, header.group(1) if header else str(sid), assignments)


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
    expression = re.sub(r'"([0-9]+)\$C"', r'INDEX("\1")', expression)
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


def evaluate(node, env):
    if type(node).__name__ in ('Constant', 'Num', 'Str', 'NameConstant'):
        return literal(node)
    if isinstance(node, ast.Name): return env[node.id]
    if isinstance(node, ast.Call):
        if not isinstance(node.func,ast.Name) or node.keywords:
            raise ValueError('Unsupported formula call')
        return env[node.func.id](*(evaluate(x,env) for x in node.args))
    if isinstance(node, ast.BoolOp):
        operands = [evaluate(x,env) for x in node.values]
        result = operands[0]
        for operand in operands[1:]:
            result = result & operand if isinstance(node.op,ast.And) else result | operand
        return result
    if isinstance(node, ast.BinOp):
        a,b=evaluate(node.left,env),evaluate(node.right,env)
        if isinstance(node.op,ast.Add): return a+b
        if isinstance(node.op,ast.Sub): return a-b
        if isinstance(node.op,ast.Mult): return a*b
        if isinstance(node.op,ast.Div): return a/b
    if isinstance(node,ast.Compare):
        a=evaluate(node.left,env); result=True
        for op,right in zip(node.ops,node.comparators):
            b=evaluate(right,env)
            close = np.isclose(a, b, rtol=0.0, atol=1e-10)
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


def bind(frame, indices, indexc, name, shares):
    """Formula names that read market data for one stock."""
    def index(code): return indices[code].reindex(frame.index)
    def finance(field):
        if field != 1: raise ValueError('Unknown FINANCE field {}'.format(field))
        if shares is None: raise MissingInput('FINANCE(1) needs a share-capital series')
        return shares
    def namelike(prefix):
        # Without a name the ST exclusion cannot be decided.
        if not name: raise MissingInput('NAMELIKE needs the stock name')
        return int(name.startswith(prefix))
    return dict(FINANCE=finance, INDEX=index, NAMELIKE=namelike,
                CLOSE=frame.close, HIGH=frame.high, LOW=frame.low, AMO=frame.amount,
                INDEXC=indexc.reindex(frame.index))


def formula_environment(strategy, frame, indices, indexc, name, shares):
    env = dict(INDICATORS)
    env.update(bind(frame, indices, indexc, name, shares))
    for key,expression in strategy.assignments:
        env[key]=evaluate(parse(expression),env)
    return env


def signals(strategy, frame, indices, indexc, name, shares):
    env = formula_environment(strategy, frame, indices, indexc, name, shares)
    return pd.DataFrame({'buy':env[BUY_KEY],'sell':env[SELL_KEY]},index=frame.index).fillna(False)
