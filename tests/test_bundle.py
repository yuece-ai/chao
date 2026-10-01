import importlib.util
import io
import shutil
import subprocess
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
    module.CONFIG.update(signal_time='00:00', order_time='00:00', account_id='A1')
    module.passorder, module.get_trade_detail_data = account.passorder, account.get_trade_detail_data
    try:
        with redirect_stdout(io.StringIO()) as out:
            module.init(C)
            module.handlebar(C)
    finally:
        module.CONFIG.clear(); module.CONFIG.update(saved)
        del module.passorder, module.get_trade_detail_data
    assert account.orders == []
    return out.getvalue()


@pytest.fixture(scope='module')
def bundled(tmp_path_factory):
    path = tmp_path_factory.mktemp('dist') / 'chao_strategy.py'
    path.write_bytes(bundle('test').encode('gbk'))
    spec = importlib.util.spec_from_file_location('chao_strategy', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, module


def test_bundle_behaves_like_the_modules(bundled):
    _, module = bundled
    assert run_strategy(module) == run_strategy(entry)


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
