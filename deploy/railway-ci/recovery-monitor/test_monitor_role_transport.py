"""Local transport checks; not part of the original ten or thirteen image tests."""
import base64
import hashlib
import json
import unittest

import test_monitor_roles as roles


class RoleLogTransportTests(unittest.TestCase):
    def receipt(self, log, **changes):
        value = {"schema": "seiche.recovery-monitor-role-tests.v1", "status": "PASS",
                 "passed": 13, "skipped": 0, "log_sha256": hashlib.sha256(log).hexdigest(),
                 "controller_source": "c" * 40, "application_source": "a" * 40,
                 "manifest_sha256": "b" * 64}
        value.update(changes)
        return (json.dumps(value, sort_keys=True) + "\n").encode()

    def test_complete_bytes_round_trip_and_receipt_link(self):
        log = b"all detailed cases retained\nRan 13 tests in 0.123s\nOK\n"
        receipt = self.receipt(log)
        record = roles.role_log_record(log, receipt)
        self.assertEqual(base64.b64decode(record["data_base64"], validate=True), log)
        self.assertEqual(record["byte_length"], len(log))
        self.assertEqual(record["sha256"], hashlib.sha256(log).hexdigest())
        self.assertEqual(record["role_receipt_sha256"], hashlib.sha256(receipt).hexdigest())
        self.assertEqual(record["controller_source"], "c" * 40)
        self.assertEqual(record["application_source"], "a" * 40)
        self.assertEqual(record["manifest_sha256"], "b" * 64)
        line = roles.LOG_MARKER + json.dumps(record, sort_keys=True) + "\n"
        self.assertLessEqual(len(line.encode()), 8192)
        self.assertNotIn("Ran 13 tests", line)

    def test_raw_log_above_limit_is_rejected(self):
        log = b"x" * 8193
        with self.assertRaisesRegex(ValueError, "Detailed role log exceeds"):
            roles.role_log_record(log, self.receipt(log))

    def test_encoded_record_above_limit_is_rejected(self):
        log = b"x" * 6000
        self.assertLess(len(log), roles.LOG_RECORD_LIMIT)
        with self.assertRaisesRegex(ValueError, "Encoded role log exceeds"):
            roles.role_log_record(log, self.receipt(log))

    def test_hash_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            roles.role_log_record(b"changed", self.receipt(b"original"))

    def test_failed_or_skipped_or_wrong_count_receipt_is_rejected(self):
        for changes in ({"status": "FAIL"}, {"passed": 12}, {"skipped": 1},
                        {"skipped": False}, {"schema": "foreign"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "does not match"):
                roles.role_log_record(b"log", self.receipt(b"log", **changes))

    def test_noncanonical_or_duplicate_receipt_is_rejected(self):
        receipt = self.receipt(b"log")
        for altered in (receipt.rstrip(), receipt.replace(b'{', b'{"passed": 13,', 1)):
            with self.subTest(altered=altered), self.assertRaisesRegex(ValueError, "not canonical"):
                roles.role_log_record(b"log", altered)


if __name__ == "__main__":
    unittest.main()
