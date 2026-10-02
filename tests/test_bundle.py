import importlib.util
import io
import shutil
import subprocess
from pathlib import Path
from contextlib import redirect_stdout
import pytest
import chao.qmt_entry as entry
from scripts.bundle_qmt import BundleError, bundle, split_module
from tests.qmt_fake import FakeAccount, FakeContextInfo, daily
from tests.test_qmt import INDEX_BARS


def run_strategy(module):
    """init + handlebar in live dry-run mode, with QMT's globals faked."""
    bars = dict(INDEX_BARS, **{'000001.SZ': daily([10.0] * 299 + [11.0])})
    C = FakeContextInfo(bars, names={'000001.SZ': 'X'}, sectors={'沪深A股': ['000001.SZ']})
    account = FakeAccount(cash=100000.0)
    saved = dict(module.CONFIG)
    module.CONFIG.update(prepare_time='00:00', signal_time='00:00', order_time='00:00', account_id='A1')
    module.passorder, module.get_trade_detail_data = account.passorder, account.get_trade_detail_data
    module.download_history_data = account.download_history_data
    try:
        with redirect_stdout(io.StringIO()) as out:
            module.init(C)
            module.handlebar(C)
    finally:
        module.CONFIG.clear(); module.CONFIG.update(saved)
        del module.passorder, module.get_trade_detail_data, module.download_history_data
    assert account.orders == []
    return out.getvalue()


@pytest.fixture(scope='module')
def bundled(tmp_path_factory):
    path = tmp_path_factory.mktemp('dist') / 'chao_strategy.py'
    path.write_bytes(bundle('test').encode('ascii'))
    spec = importlib.util.spec_from_file_location('chao_strategy', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, module


def test_bundle_behaves_like_the_modules(bundled):
    _, module = bundled
    bundled_log = run_strategy(module).replace('chao: build {} '.format(module.BUILD), 'chao: build source ')
    assert bundled_log == run_strategy(entry)


def test_bundle_reads_gui_parameters_from_its_globals(bundled):
    _, module = bundled
    module.strategies = 2  # what QMT does with a GUI parameter
    try:
        assert 'chao: config strategies = (2,)  (gui)' in run_strategy(module)
    finally:
        del module.strategies


@pytest.mark.skipif(shutil.which('vermin') is None, reason='vermin not installed')
def test_bundle_runs_on_python_36(bundled):
    path, _ = bundled
    result = subprocess.run(['vermin', '--target=3.6-', '--no-tips', '--violations', str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_split_module_drops_package_imports_and_rejects_import_chao():
    imports, body, names = split_module('import re\nfrom chao.market import X\nY = X\n')
    assert imports == [(None, 're', None)] and body == 'Y = X' and names == ['Y']
    with pytest.raises(BundleError):
        split_module('import chao.market\n')


def test_bundle_writes_the_local_config_after_checking_it():
    text = bundle('test', {'account_id': 'A1', 'max_positions': 5})
    assert "\nCONFIG = {'account_id': 'A1', 'max_positions': 5}\n" in text
    with pytest.raises(ValueError, match="unknown keys \\['max_position'\\]"):
        bundle('test', {'max_position': 5})


def test_committed_qmt_file_matches_the_sources():
    """qmt/chao_strategy.py is what bundle_qmt.py builds from qmt/strategy.json."""
    import json
    from pathlib import Path
    committed = Path('qmt/chao_strategy.py').read_bytes().decode('ascii')
    revision = committed.splitlines()[1].split(' from ')[1].split(';')[0]
    config = json.loads(Path('qmt/strategy.json').read_text(encoding='utf-8'))
    assert committed == bundle(revision, config), \
        'rebuild: python scripts/bundle_qmt.py --config qmt/strategy.json --out qmt/chao_strategy.py'


def test_bundle_is_pure_ascii_and_keeps_the_chinese_values():
    """Pure ASCII survives copy and paste into QMT in any encoding."""
    text = bundle('test')
    assert text.isascii()
    namespace = {}
    exec(compile(text, 'chao_strategy.py', 'exec'), namespace)
    assert namespace['BUY_KEY'] == '买入条件'
    assert any('震荡突破' in source for source in namespace['STRATEGY_FILES'].values())


def test_bundle_globals_leave_qmt_names_alone():
    # ContextInfo.start/end carry the backtest range; no global may shadow a QMT name.
    import ast
    tree = ast.parse(Path('qmt/chao_strategy.py').read_text())
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    names |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    reserved = {'start', 'end', 'capital', 'benchmark', 'period', 'stop', 'after_init', 'account', 'accountType'}
    assert names & reserved == set()


def test_the_log_names_the_pasted_build(bundled):
    path, module = bundled
    fingerprint = path.read_text().splitlines()[2].split()[2].rstrip(':')
    assert module.BUILD == fingerprint and len(fingerprint) == 8
    assert 'chao: build {} init'.format(fingerprint) in run_strategy(module)
