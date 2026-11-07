"""Exercise module CLI dispatch without importing or running real collectors."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from seiche import hetzner_runtime as runtime


@pytest.mark.parametrize('kind', ['market', 'source'])
def test_module_worker_entrypoint_uses_canonical_authority_context(tmp_path, kind):
    """Run the real -m dispatcher and observe authority from collector imports."""
    (tmp_path / 'sitecustomize.py').write_text('''
import sys
from types import ModuleType
from seiche import hetzner_runtime as runtime

def initialize():
    runtime._context = {'entrypoint_test': 'canonical'}

async def collect(**kwargs):
    from seiche import hetzner_runtime as imported
    assert imported._context == {'entrypoint_test': 'canonical'}
    print('COLLECTOR_SAW_CANONICAL_AUTHORITY_CONTEXT')

runtime.initialize = initialize
for name, function in [('market_runtime', 'run_worker'), ('ingest_runtime', 'run_legacy_worker')]:
    module = ModuleType('seiche.' + name)
    setattr(module, function, collect)
    sys.modules[module.__name__] = module
''')
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'seiche.hetzner_runtime', '--worker', kind],
        cwd=tmp_path, env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
                          'PYTHONPATH': os.pathsep.join([str(tmp_path), str(Path(runtime.__file__).parents[1])])},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'COLLECTOR_SAW_CANONICAL_AUTHORITY_CONTEXT'


def test_module_supervisor_entrypoint_uses_canonical_runtime(tmp_path):
    (tmp_path / 'sitecustomize.py').write_text('''
from seiche import hetzner_runtime as runtime
def supervise():
    print('CANONICAL_SUPERVISOR_CALLED')
    return 0
runtime.supervise = supervise
''')
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'seiche.hetzner_runtime'],
        cwd=tmp_path, env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
                          'PYTHONPATH': os.pathsep.join([str(tmp_path), str(Path(runtime.__file__).parents[1])])},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'CANONICAL_SUPERVISOR_CALLED'
