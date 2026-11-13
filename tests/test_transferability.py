from __future__ import annotations

import unittest

from meme_flight_recorder.transferability import (
    SPL_TOKEN_PROGRAM,
    TOKEN_2022_PROGRAM,
    Transferability,
    assess_transferability,
)


class ClassicSplTests(unittest.TestCase):
    """Classic SPL has no hook for custom transfer logic."""

    def test_revoked_freeze_authority_is_free(self):
        report = assess_transferability(SPL_TOKEN_PROGRAM, (), True)
        self.assertEqual(report.verdict, Transferability.FREE)
        self.assertIs(report.sellable, True)

    def test_live_freeze_authority_is_restricted(self):
        report = assess_transferability(SPL_TOKEN_PROGRAM, (), False)
        self.assertEqual(report.verdict, Transferability.RESTRICTED)
        self.assertIs(report.sellable, False)
        self.assertIn("freeze_authority_active", report.reasons)

    def test_unknown_freeze_authority_stays_unknown(self):
        report = assess_transferability(SPL_TOKEN_PROGRAM, (), None)
        self.assertEqual(report.verdict, Transferability.UNKNOWN)
        self.assertIsNone(report.sellable)


class Token2022Tests(unittest.TestCase):
    def test_transfer_hook_is_restricted(self):
        report = assess_transferability(TOKEN_2022_PROGRAM, ("transferHook",), True)
        self.assertEqual(report.verdict, Transferability.RESTRICTED)
        self.assertIn("token2022_transferHook", report.reasons)

    def test_permanent_delegate_is_restricted(self):
        """A permanent delegate can move or burn a holder's balance."""
        report = assess_transferability(TOKEN_2022_PROGRAM, ("permanentDelegate",), True)
        self.assertIs(report.sellable, False)

    def test_non_transferable_is_restricted(self):
        report = assess_transferability(TOKEN_2022_PROGRAM, ("nonTransferable",), True)
        self.assertIs(report.sellable, False)

    def test_harmless_extensions_remain_free(self):
        report = assess_transferability(
            TOKEN_2022_PROGRAM, ("metadataPointer", "tokenMetadata"), True
        )
        self.assertEqual(report.verdict, Transferability.FREE)

    def test_restriction_beats_a_revoked_freeze_authority(self):
        report = assess_transferability(
            TOKEN_2022_PROGRAM, ("tokenMetadata", "transferFeeConfig"), True
        )
        self.assertIs(report.sellable, False)
        self.assertEqual(report.restrictive_extensions, ("transferFeeConfig",))


class UnknownProgramTests(unittest.TestCase):
    def test_unrecognised_program_is_never_cleared(self):
        report = assess_transferability("SomeOtherProgram1111111111111111", (), True)
        self.assertEqual(report.verdict, Transferability.UNKNOWN)
        self.assertIsNone(report.sellable)

    def test_missing_program_is_unknown(self):
        report = assess_transferability("", (), True)
        self.assertEqual(report.verdict, Transferability.UNKNOWN)

    def test_unknown_is_not_a_soft_pass(self):
        """The gate fails closed on None, which is the intended behaviour."""
        self.assertIsNone(assess_transferability("", (), None).sellable)


if __name__ == "__main__":
    unittest.main()
