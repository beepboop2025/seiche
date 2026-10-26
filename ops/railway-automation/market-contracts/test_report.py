import unittest

from report import validate_proof, validate_cold_result, prove_runtime_isolation


class ReportProofTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "source": "a" * 40,
            "deployment": "12345678-1234-4234-8234-123456789abc",
            "head": "a" * 40,
            "admission": "RAILWAY_MARKET_SOURCE_ADMITTED source=" + "a" * 40 + " base=" + "b" * 40 + "\n",
            "build_log": "10 passed\nRAILWAY_MARKET_CONTRACTS_BUILD_PASS source=" + "a" * 40 + "\n",
        }

    def test_matching_completed_image(self):
        self.assertEqual(validate_proof(**self.values), self.values["build_log"].splitlines()[-1])

    def test_mismatched_or_incomplete_proof_is_rejected(self):
        cases = [
            ("source", ""), ("source", "b" * 40), ("head", "b" * 40),
            ("deployment", "unavailable"), ("deployment", ""),
            ("admission", self.values["admission"].replace("a" * 40, "c" * 40)),
            ("admission", self.values["admission"] * 2),
            ("admission", self.values["admission"].replace("b" * 40, "unavailable")),
            ("build_log", "10 failed\n"),
            ("build_log", self.values["build_log"].replace("a" * 40, "c" * 40)),
            ("build_log", self.values["build_log"] * 2),
        ]
        for key, value in cases:
            with self.subTest(field=key, value=value):
                with self.assertRaises(ValueError):
                    validate_proof(**{**self.values, key: value})



class ColdRuntimeResultTests(unittest.TestCase):
    def result(self, counts="", cases=""):
        kernel = '<testcase name="test_real_linux_kernel_denies_native_and_descendant_production_writes"/>'
        return '<testsuites><testsuite ' + counts + '>' + kernel + cases + '</testsuite></testsuites>'

    def test_real_completed_kernel_proof(self):
        self.assertEqual(validate_cold_result(self.result()), 1)

    def test_failed_skipped_missing_and_inconsistent_results_are_rejected(self):
        values = [
            '<testsuites/>', '<testsuites><testsuite><testcase name="unrelated"/></testsuite></testsuites>',
            self.result('skipped="1"'), self.result('failures="1"'), self.result('errors="1"'),
            self.result(cases='<testcase name="hidden"><skipped/></testcase>'),
            self.result(cases='<testcase name="hidden"><failure/></testcase>'),
            self.result(cases='<testcase name="hidden"><error/></testcase>'),
        ]
        for raw in values:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_cold_result(raw)

    def test_nonroot_runtime_is_rejected_before_tests(self):
        from unittest.mock import patch
        with patch("report.os.getuid", return_value=10001), self.assertRaises(ValueError):
            prove_runtime_isolation()

    def test_failed_runtime_tests_cannot_become_pass(self):
        import subprocess
        from unittest.mock import patch
        with patch("report.os.getuid", return_value=0), patch("report.os.getgid", return_value=0), \
                patch("report.subprocess.run", side_effect=subprocess.CalledProcessError(1, "pytest")), \
                self.assertRaises(subprocess.CalledProcessError):
            prove_runtime_isolation()

if __name__ == "__main__":
    unittest.main()
