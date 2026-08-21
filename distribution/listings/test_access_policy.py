"""Contracts for catalog access-policy copy."""

import unittest
from typing import cast

from distribution.listings.access_policy import AccessMode, gated_access_statement


class AccessPolicyContracts(unittest.TestCase):
    def test_every_mode_preserves_the_public_commitment(self) -> None:
        for mode in ("subscriber", "invite_or_grant", "operator_cost"):
            statement = gated_access_statement(cast(AccessMode, mode))
            with self.subTest(mode=mode):
                self.assertIn("five compute-heavy tools", statement)
                self.assertIn("eleven public evidence tools", statement)
                self.assertIn("remain anonymous and free", statement)
                self.assertEqual(statement.count("."), 1)

    def test_unknown_runtime_mode_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported listing access mode"):
            gated_access_statement(cast(AccessMode, "unknown"))


if __name__ == "__main__":
    unittest.main()
