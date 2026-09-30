"""Evaluate the original formula expressions using a restricted AST."""
import ast
import re
from pathlib import Path
import numpy as np
import pandas as pd
from chao.data import MissingInput


def strategies(path):
    text = Path(path).read_text(encoding='utf-8')
    text = re.sub(r'\{[^}]*\}', '', text)
    matches = list(re.finditer(r'策略([1-7])-([^\s：:]+)[：:]?', text))
    result = {}
    for i, match in enumerate(matches):
        section = text[match.end(): matches[i+1].start() if i+1 < len(matches) else len(text)]
        assignments = re.findall(r'([A-Za-z][A-Za-z0-9]*|买入条件|卖出条件)\s*:=\s*([^;]+);', section)
        if not assignments or assignments[-1][0] != '卖出条件':
            raise ValueError(f'Incomplete source formula: {match.group(0)}')
        result[int(match.group(1))] = {'name': match.group(2), 'assignments': assignments}
    if set(result) != set(range(1,8)):
        raise ValueError('Expected exactly seven original strategies')
    return result


def MA(x, n): return x.rolling(int(n), min_periods=int(n)).mean()
def REF(x, n): return x.shift(int(n))
def HHV(x, n): return x.rolling(int(n), min_periods=int(n)).max()
def LLV(x, n): return x.rolling(int(n), min_periods=int(n)).min()


def parse(expression):
    expression = re.sub(r'"([0-9]+)\$C"', r'INDEX("\1")', expression)
    expression = re.sub(r'\bAND\b', 'and', expression)
    expression = re.sub(r'\bOR\b', 'or', expression)
    expression = re.sub(r'(?<![<>=!])=(?!=)', '==', expression)
    return ast.parse(expression.strip(), mode='eval').body


def evaluate(node, env):
    if isinstance(node, ast.Constant): return node.value
    if isinstance(node, ast.Name): return env[node.id]
    if isinstance(node, ast.Call):
        if not isinstance(node.func,ast.Name) or node.keywords:
            raise ValueError('Unsupported formula call')
        return env[node.func.id](*(evaluate(x,env) for x in node.args))
    if isinstance(node, ast.BoolOp):
        values = [evaluate(x,env) for x in node.values]
        result = values[0]
        for value in values[1:]:
            result = result & value if isinstance(node.op,ast.And) else result | value
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
    raise ValueError(f'Unsupported formula AST: {ast.dump(node)}')


def formula_environment(strategy, frame, indices, indexc, name, shares):
    def index(code): return indices[code].reindex(frame.index)
    def finance(field):
        if field != 1: raise ValueError(f'Unknown FINANCE field {field}')
        if shares is None: raise MissingInput('FINANCE(1) needs a share-capital series')
        return shares
    env = dict(MA=MA,REF=REF,HHV=HHV,LLV=LLV,FINANCE=finance,
               # Missing names must not pass the formula's ST exclusion.
               NAMELIKE=lambda prefix: (None if not name else int(name.startswith(prefix))),INDEX=index,
               CLOSE=frame.close,HIGH=frame.high,LOW=frame.low,AMO=frame.amount,
               INDEXC=indexc.reindex(frame.index))
    for key,expression in strategy['assignments']:
        env[key]=evaluate(parse(expression),env)
    return env


def signals(strategy, frame, indices, indexc, name, shares):
    env = formula_environment(strategy, frame, indices, indexc, name, shares)
    return pd.DataFrame({'buy':env['买入条件'],'sell':env['卖出条件']},index=frame.index).fillna(False)
