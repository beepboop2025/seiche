"""Run the exact native-image gate cases in backend source qualification too."""
import subprocess
import sys
from pathlib import Path
import unittest


class NativeInstallationGateSourceTests(unittest.TestCase):
    def test_native_image_admission_contract(self):
        directory = Path(__file__).resolve().parents[2] / 'deploy/railway-ci/recovery-controller'
        result = subprocess.run([sys.executable, '-m', 'unittest', '-v', 'test_native_installation_gate'],
                                cwd=directory, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ran 13 tests', result.stderr)
        self.assertNotIn('skipped=', result.stderr)
