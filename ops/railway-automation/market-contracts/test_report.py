import unittest

from report import validate_proof


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


if __name__ == "__main__":
    unittest.main()
