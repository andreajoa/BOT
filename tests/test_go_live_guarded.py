# -*- coding: utf-8 -*-
import unittest

from scripts.go_live_guarded import approval_matches, approval_phrase


class GuardedLiveApprovalTests(unittest.TestCase):
    def test_expected_phrase_contains_exact_command_id(self):
        self.assertEqual(approval_phrase("abc123"), "APROVAR abc123")

    def test_exact_phrase_approves(self):
        self.assertTrue(approval_matches("APROVAR abc123", "abc123"))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertTrue(approval_matches("  APROVAR abc123\n", "abc123"))

    def test_generic_yes_does_not_approve(self):
        self.assertFalse(approval_matches("sim", "abc123"))
        self.assertFalse(approval_matches("yes", "abc123"))

    def test_missing_or_wrong_command_id_does_not_approve(self):
        self.assertFalse(approval_matches("APROVAR", "abc123"))
        self.assertFalse(approval_matches("APROVAR xyz999", "abc123"))

    def test_case_change_does_not_approve(self):
        self.assertFalse(approval_matches("aprovar abc123", "abc123"))


if __name__ == "__main__":
    unittest.main()
