#!/usr/bin/env python3
"""Bundle the QMT-bound modules into one strategy file for the QMT client.

QMT's built-in strategies are a single GBK-encoded Python 3.6 file. The
modules listed in MODULES are concatenated in that order:
- `from chao.x import ...` lines are dropped, because everything shares one
  namespace;
- other imports are hoisted to the top and deduplicated;
- a top-level name defined twice fails the build;
- chao/catalog.py is replaced by the strategy files embedded as a literal;
- the output is pure ASCII: non-ASCII text in strings becomes \\u escapes
  (same values at run time) and in comments becomes the escape text, so the
  file can be pasted into QMT's editor whatever the encoding on the way;
- with --config, the local settings file (e.g. qmt.json, which holds the
  account and is not committed) becomes CONFIG, after it is checked
  against the entry's fields.
"""
import argparse
import ast
import io
import json
import subprocess
import tokenize
from pathlib import Path
from chao.catalog import strategy_files

ROOT = Path(__file__).resolve().parent.parent
# Dependency order: each module only uses names from modules above it.
MODULES = ['indicators', 'settings', 'market', 'formulas', 'qfq', 'signals', 'orders', 'replay',
           'tdx_report', 'qmt_source', 'qmt_trade', 'catalog', 'qmt_entry']


class BundleError(Exception):
    pass


def top_level_names(tree):
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def split_module(source):
    """(imports, body text, top-level names) of one module.

    imports are (module, name, alias) triples; module is None for `import x`.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    drop, imports = set(), []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            drop.update(range(node.lineno - 1, node.end_lineno))
        if isinstance(node, ast.ImportFrom):
            if (node.module or '').startswith('chao'):
                continue
            imports += [(node.module, a.name, a.asname) for a in node.names]
        elif isinstance(node, ast.Import):
            if any(a.name.startswith('chao') for a in node.names):
                raise BundleError('use "from chao.x import name", not "import chao"')
            imports += [(None, a.name, a.asname) for a in node.names]
    body = '\n'.join(line for i, line in enumerate(lines) if i not in drop).strip()
    return imports, body, top_level_names(tree)


def render_imports(imports):
    """`import x` lines first, then one sorted `from m import ...` per module."""
    alias = lambda name, asname: name + (' as ' + asname if asname else '')
    plain = sorted({alias(n, a) for m, n, a in imports if m is None})
    grouped = {}
    for module, name, asname in imports:
        if module is not None:
            grouped.setdefault(module, set()).add(alias(name, asname))
    return (['import ' + p for p in plain] +
            ['from {} import {}'.format(m, ', '.join(sorted(n))) for m, n in sorted(grouped.items())])


def catalog_module():
    files = strategy_files()
    return ('# Strategy formula files embedded from strategies/.\n'
            'STRATEGY_FILES = {!r}\n\n\n'
            'def strategy_files():\n'
            '    return dict(STRATEGY_FILES)').format(files)


def checked_config(config):
    """Reject unknown keys and bad values now rather than in the client."""
    from chao.qmt_entry import FIELDS
    from chao.settings import load
    load(FIELDS, [('config', config)])
    return config


def ascii_only(text):
    """The same program with every non-ASCII character escaped."""
    escape = lambda s: s.encode('ascii', 'backslashreplace').decode('ascii')
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        value = tok.string
        if not value.isascii():
            if tok.type == tokenize.STRING:
                value = ascii(ast.literal_eval(value))
            elif tok.type == tokenize.COMMENT:
                value = escape(value)
            else:
                raise BundleError('non-ASCII outside strings and comments: {!r}'.format(value))
        pieces.append((tok.type, value, tok.start, tok.end, tok.line))
    return tokenize.untokenize(pieces)


def bundle(revision, config=None):
    imports, sections, owner = [], [], {}
    for name in MODULES:
        path = 'chao/{}.py'.format(name)
        source = catalog_module() if name == 'catalog' else (ROOT / path).read_text(encoding='utf-8')
        if name == 'qmt_entry' and config:
            if source.count('\nCONFIG = {}\n') != 1:
                raise BundleError('chao/qmt_entry.py must define CONFIG = {} exactly once')
            source = source.replace('\nCONFIG = {}\n', '\nCONFIG = {!r}\n'.format(checked_config(config)))
        found, body, defined = split_module(source)
        for symbol in defined:
            if symbol in owner:
                raise BundleError('{} defined in both {} and {}'.format(symbol, owner[symbol], path))
            owner[symbol] = path
        imports += found
        sections.append('# ---- {} ----\n{}'.format(path, body))
    header = ['# -*- coding: ascii -*-',
              '# Generated by scripts/bundle_qmt.py from {}; do not edit.'.format(revision),
              '# Paste into a QMT strategy (or copy the file). Entry points: init(C), handlebar(C).']
    text = '\n'.join(header + [''] + render_imports(imports)) + '\n\n\n' + '\n\n\n'.join(sections) + '\n'
    return ascii_only(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='dist/chao_strategy.py')
    ap.add_argument('--config', help='local settings JSON (see qmt.example.json) written into CONFIG')
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8')) if args.config else None
    revision = subprocess.run(['git', 'describe', '--always', '--dirty'], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    text = bundle(revision, config)
    compile(text, args.out, 'exec')
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode('ascii'))
    print('{} ({} lines, {})'.format(out, text.count('\n'), revision))


if __name__ == '__main__':
    main()
