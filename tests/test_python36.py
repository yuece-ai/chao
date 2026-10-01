"""Modules that run inside QMT must work on its bundled Python 3.6."""
import shutil
import subprocess
import pytest

QMT_MODULES = ['chao/indicators.py', 'chao/formulas.py', 'chao/qfq.py', 'chao/settings.py',
               'chao/market.py', 'chao/signals.py']


@pytest.mark.skipif(shutil.which('vermin') is None, reason='vermin not installed')
def test_qmt_modules_run_on_python_36():
    result = subprocess.run(['vermin', '--target=3.6-', '--no-tips', '--violations', *QMT_MODULES],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
